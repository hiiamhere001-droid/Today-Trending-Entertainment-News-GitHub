"""Collect today's US entertainment stories, match Google Trends (US), write news.json.
Install: pip install feedparser requests beautifulsoup4
"""
import json, re, time, datetime as dt
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import feedparser, requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; EntertainmentTrendsBot/1.0)"}
US_TZ = ZoneInfo("America/New_York")

# Feed addresses to try first for sites where homepage discovery is unreliable.
# These are unverified guesses; the script falls back to auto-discovery.
FEED_HINTS = {
    "yahoo.com": ["https://www.yahoo.com/entertainment/rss", "https://www.yahoo.com/news/rss"],
    "profiles.yahoo.com": [],
    "usmagazine.com": ["https://www.usmagazine.com/feed/"],
    "collider.com": ["https://collider.com/feed/"],
    "news.meaww.com": ["https://news.meaww.com/feed"],
}
# Sites that publish only entertainment: accept everything except the block list.
ENT_ONLY = {"usmagazine.com", "collider.com"}

STRONG = re.compile(
    r"\b(movie|movies|film|films|trailer|box office|netflix|hulu|disney\+?|hbo|peacock|"
    r"prime video|apple tv|streaming|series|season|episode|sitcom|reality tv|bachelor|"
    r"singer|rapper|album|concert|tour|song|single|grammys?|oscars?|emmys?|golden globes?|"
    r"awards?|actor|actress|hollywood|red carpet|celebrity|celebs?|kardashian|"
    r"divorce|engaged|engagement|wedding|marries|dating|pregnant|baby|split|breakup|"
    r"spotted|premiere|cast|director|sequel|star wars|marvel|taylor swift|beyonc[eé]|"
    r"britney|royal|late[- ]night|talk show|snl|broadway)\b", re.I)
BLOCK = re.compile(
    r"\b(politic\w*|election|senate|congress|nfl|nba|mlb|nhl|stocks?|crypto|recipe|"
    r"shopping|deals?|amazon|walmart|bigfoot|ufo|weather|crash|shooting|war)\b", re.I)

CATEGORIES = [
    ("Awards", r"\b(awards?|oscars?|emmys?|grammys?|golden globes?|nominat\w*)\b"),
    ("Streaming", r"\b(netflix|hulu|disney\+?|hbo|peacock|prime video|apple tv|streaming|paramount\+)\b"),
    ("Movies", r"\b(movie|movies|film|films|box office|trailer|sequel|director)\b"),
    ("TV", r"\b(tv|series|season|episode|sitcom|reality|bachelor|talk show|snl)\b"),
    ("Music", r"\b(singer|rapper|album|concert|tour|song|single|band|music)\b"),
    ("Fashion", r"\b(fashion|red carpet|dress|outfit|met gala|style)\b"),
]

def get(url):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        return r
    except Exception as e:
        print("   fail:", url, str(e)[:80])
        return None

def candidate_feeds(site, host):
    out = list(FEED_HINTS.get(host, []))
    r = get(site)
    if r:
        soup = BeautifulSoup(r.text, "html.parser")
        for l in soup.find_all("link", rel="alternate"):
            t = (l.get("type") or "")
            if "rss" in t or "atom" in t:
                out.append(urljoin(site, l.get("href")))
    base = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
    out += [base + p for p in ("/feed/", "/rss", "/rss.xml", "/feed")]
    seen, res = set(), []
    for f in out:
        if f not in seen:
            seen.add(f); res.append(f)
    return res

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
    return dt.datetime.fromtimestamp(time.mktime(t), dt.timezone.utc) if t else None

def trends_us():
    r = get("https://trends.google.com/trending/rss?geo=US")
    if not r:
        return []
    return [e.get("title", "").lower() for e in feedparser.parse(r.content).entries if e.get("title")]

def classify(text):
    for name, pat in CATEGORIES:
        if re.search(pat, text, re.I):
            return name
    return "Celebrity"

def is_entertainment(host, title, tags, url):
    text = f"{title} {tags} {urlparse(url).path.replace('-', ' ').replace('/', ' ')}"
    if BLOCK.search(text):
        return False
    if host in ENT_ONLY:
        return True
    return bool(STRONG.search(text)) or "/entertainment/" in url

def main():
    sites = json.load(open("sites.json"))
    trends = trends_us()
    print("trending terms:", len(trends))
    today = dt.datetime.now(US_TZ).date()
    items, status, seen_urls = [], {}, set()
    for site in sites:
        if isinstance(site, dict):
            site = site["url"]
        host = urlparse(site).netloc.replace("www.", "")
        kept = total = 0
        used = None
        for feed_url in candidate_feeds(site, host):
            r = get(feed_url)
            if not r:
                continue
            feed = feedparser.parse(r.content)
            if not feed.entries:
                continue
            used = feed_url
            for e in feed.entries:
                total += 1
                when = entry_time(e)
                if not when or when.astimezone(US_TZ).date() != today:
                    continue
                url = e.get("link")
                if not url or url in seen_urls:
                    continue
                title = e.get("title", "").strip()
                tags = " ".join(t.get("term", "") for t in e.get("tags", []))
                if not is_entertainment(host, title, tags, url):
                    continue
                seen_urls.add(url)
                low = f"{title} {e.get('summary','')[:300]}".lower()
                hit = next((t for t in trends if len(t) > 3 and t in low), None)
                items.append({"title": title, "url": url, "source": host,
                              "category": classify(f"{title} {tags} {url}"),
                              "image": entry_image(e), "published": when.isoformat(),
                              "trending": bool(hit), "trend_term": hit})
                kept += 1
            break
        status[host] = kept
        print(f"{host}: feed={used} entries={total} kept_today={kept}")
    items.sort(key=lambda i: i["published"], reverse=True)
    items.sort(key=lambda i: not i["trending"])
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "date_us": str(today), "per_site": status, "items": items}
    json.dump(out, open("news.json", "w"), ensure_ascii=False, indent=1)
    print("saved", len(items), "stories")

if __name__ == "__main__":
    main()
