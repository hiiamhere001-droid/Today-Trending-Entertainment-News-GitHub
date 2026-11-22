"""Collect today's US entertainment stories from the sites in sites.json,
match them to Google Trends (US), and write news.json.
Install: pip install feedparser requests beautifulsoup4
Run:     python fetch_news.py
"""
import json, re, time, datetime as dt
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import feedparser, requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; EntertainmentTrendsBot/1.0)"}
US_TZ = ZoneInfo("America/New_York")
ENT_WORDS = re.compile(
    r"celebrit|entertain|movie|film|tv|series|show|music|singer|album|concert|"
    r"actor|actress|star|hollywood|netflix|streaming|oscar|emmy|grammy|award|"
    r"reality|royal|trailer|box office|red carpet|couple|dating|divorce", re.I)
CATEGORIES = {
    "Music": r"music|singer|album|concert|tour|song|grammy|rapper",
    "Movies": r"movie|film|box office|trailer|oscar|director",
    "TV": r"\btv\b|series|season|episode|reality|sitcom|emmy",
    "Streaming": r"netflix|hulu|disney\+|prime video|apple tv|max\b|streaming",
    "Awards": r"award|oscar|emmy|grammy|golden globe|nominat",
    "Fashion": r"fashion|red carpet|dress|outfit|style",
}

def get(url, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=20, **kw)
        r.raise_for_status()
        return r
    except Exception as e:
        print("  fail", url, e)
        return None

def discover_feeds(site):
    feeds = []
    r = get(site)
    if r:
        soup = BeautifulSoup(r.text, "html.parser")
        for l in soup.find_all("link", rel="alternate"):
            if "rss" in (l.get("type") or "") or "atom" in (l.get("type") or ""):
                feeds.append(urljoin(site, l.get("href")))
    base = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
    feeds += [base + p for p in ("/feed/", "/rss", "/rss.xml", "/feed")]
    seen, out = set(), []
    for f in feeds:
        if f not in seen:
            seen.add(f); out.append(f)
    return out

def entry_image(e):
    for k in ("media_content", "media_thumbnail"):
        v = e.get(k)
        if v and v[0].get("url"):
            return v[0]["url"]
    for l in e.get("links", []):
        if l.get("type", "").startswith("image"):
            return l.get("href")
    html = (e.get("summary") or "") + "".join(c.get("value", "") for c in e.get("content", []))
    m = re.search(r'<img[^>]+src="([^"]+)"', html)
    return m.group(1) if m else None

def entry_time(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    if not t:
        return None
    return dt.datetime.fromtimestamp(time.mktime(t), dt.timezone.utc)

def trends_us():
    r = get("https://trends.google.com/trending/rss?geo=US")
    if not r:
        return []
    f = feedparser.parse(r.content)
    return [e.get("title", "").lower() for e in f.entries if e.get("title")]

def classify(text):
    for name, pat in CATEGORIES.items():
        if re.search(pat, text, re.I):
            return name
    return "Celebrity"

def main():
    sites = json.load(open("sites.json"))
    trends = trends_us()
    print("trending terms:", len(trends))
    today = dt.datetime.now(US_TZ).date()
    items, status = [], {}
    for site in sites:
        host = urlparse(site).netloc.replace("www.", "")
        got = 0
        for feed_url in discover_feeds(site):
            r = get(feed_url)
            if not r:
                continue
            feed = feedparser.parse(r.content)
            if not feed.entries:
                continue
            for e in feed.entries:
                when = entry_time(e)
                if not when or when.astimezone(US_TZ).date() != today:
                    continue
                title = e.get("title", "").strip()
                tags = " ".join(t.get("term", "") for t in e.get("tags", []))
                blob = f"{title} {tags} {e.get('link','')} {e.get('summary','')[:300]}"
                if not ENT_WORDS.search(blob):
                    continue
                low = blob.lower()
                hit = next((t for t in trends if len(t) > 3 and t in low), None)
                items.append({"title": title, "url": e.get("link"), "source": host,
                              "category": classify(blob), "image": entry_image(e),
                              "published": when.isoformat(), "trending": bool(hit),
                              "trend_term": hit})
                got += 1
            break  # first working feed is enough
        status[host] = got
        print(host, "->", got, "stories today")
    items.sort(key=lambda i: (not i["trending"], i["published"]), reverse=False)
    items.sort(key=lambda i: i["published"], reverse=True)
    items.sort(key=lambda i: not i["trending"])
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "date_us": str(today), "per_site": status, "items": items}
    json.dump(out, open("news.json", "w"), ensure_ascii=False, indent=1)
    print("saved", len(items), "stories")

if __name__ == "__main__":
    main()
