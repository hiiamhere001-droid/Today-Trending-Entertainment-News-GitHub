"""Collect today's US entertainment stories, match Google Trends (US), write news.json.
Sites with an RSS feed use it. Sites without one (Yahoo, Jang English) are scraped:
the script reads the listing page, opens each article and reads its publish time.
Install: pip install feedparser requests beautifulsoup4
"""
import json, re, time, datetime as dt
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import feedparser, requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
US_TZ = ZoneInfo("America/New_York")

FEED_HINTS = {
    "usmagazine.com": ["https://www.usmagazine.com/feed/"],
    "collider.com": ["https://collider.com/feed/"],
    "news.meaww.com": ["https://news.meaww.com/feed"],
}
# Sites with no RSS: listing pages to scan and the article-link pattern to keep.
SCRAPE = {
    "yahoo.com": {"lists": ["https://www.yahoo.com/entertainment/",
                            "https://www.yahoo.com/entertainment/celebrity/",
                            "https://www.yahoo.com/entertainment/music/"],
                  "pattern": r"^https://www\.yahoo\.com/entertainment/[^?#]+\.html$"},
    "profiles.yahoo.com": {"lists": ["https://profiles.yahoo.com/brands/us-weekly/"],
                           "pattern": r"^https://www\.yahoo\.com/[^?#]+\.html$"},
    "jang.com.pk": {"lists": ["https://jang.com.pk/en/", "https://jang.com.pk/en"],
                    "pattern": r"^https://jang\.com\.pk/en/\d+-[^?#]+$"},
}
ENT_ONLY = {"usmagazine.com", "collider.com", "yahoo.com", "profiles.yahoo.com"}
MAX_ARTICLES = 30

STRONG = re.compile(
    r"\b(movie|movies|film|films|trailer|box office|netflix|hulu|disney\+?|hbo|peacock|"
    r"prime video|apple tv|streaming|series|season|episode|sitcom|reality tv|bachelor|"
    r"singer|rapper|album|concert|tour|song|single|grammys?|oscars?|emmys?|golden globes?|"
    r"awards?|actor|actress|hollywood|red carpet|celebrity|celebs?|kardashian|"
    r"divorce|engaged|engagement|wedding|marries|dating|pregnant|baby|split|breakup|"
    r"spotted|premiere|cast|director|sequel|star wars|marvel|taylor swift|beyonc[eé]|"
    r"britney|royal|late[- ]night|talk show|snl|broadway|kate middleton|prince|princess)\b", re.I)
BLOCK = re.compile(
    r"\b(politic\w*|election|senate|congress|nfl|nba|mlb|nhl|stocks?|crypto|recipe|"
    r"shopping|deals?|amazon|walmart|bigfoot|ufo|weather|crash|shooting|war|"
    r"classic books|reading list|novels?|gaming|video games?|tabletop|wrestling)\b", re.I)
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

def parse_dt(s):
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None

# ---------- RSS mode ----------
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

def rss_entries(site, host):
    for feed_url in candidate_feeds(site, host):
        r = get(feed_url)
        if not r:
            continue
        feed = feedparser.parse(r.content)
        if not feed.entries:
            continue
        out = []
        for e in feed.entries:
            t = e.get("published_parsed") or e.get("updated_parsed")
            when = dt.datetime.fromtimestamp(time.mktime(t), dt.timezone.utc) if t else None
            out.append({"title": e.get("title", "").strip(), "url": e.get("link"),
                        "when": when, "image": entry_image(e),
                        "tags": " ".join(x.get("term", "") for x in e.get("tags", [])),
                        "summary": e.get("summary", "")[:300]})
        return feed_url, out
    return None, []

# ---------- Scrape mode ----------
def listing_links(cfg):
    links = []
    for lp in cfg["lists"]:
        r = get(lp)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            u = urljoin(lp, a["href"]).split("#")[0].split("?")[0]
            if re.search(cfg["pattern"], u) and u not in links:
                links.append(u)
    return links[:MAX_ARTICLES]

def article_info(url):
    r = get(url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if t and t.get("content"):
                return t["content"]
    raw = meta("article:published_time", "og:article:published_time", "datePublished", "pubdate")
    if not raw:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', r.text)
        raw = m.group(1) if m else None
    if not raw:
        t = soup.find("time", attrs={"datetime": True})
        raw = t["datetime"] if t else None
    title = meta("og:title", "twitter:title") or (soup.title.string if soup.title else "")
    return {"title": (title or "").strip(), "url": url, "when": parse_dt(raw) if raw else None,
            "image": meta("og:image", "twitter:image"),
            "tags": meta("article:tag", "keywords") or "",
            "summary": (meta("og:description", "description") or "")[:300]}

def scrape_entries(cfg):
    links = listing_links(cfg)
    print("   listing links found:", len(links))
    out = []
    for u in links:
        info = article_info(u)
        if info:
            out.append(info)
        time.sleep(0.3)
    return "scraped", out

# ---------- Shared ----------
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
        if host in SCRAPE:
            used, entries = scrape_entries(SCRAPE[host])
        else:
            used, entries = rss_entries(site, host)
        kept = dated = 0
        for e in entries:
            when = e["when"]
            if not when:
                continue
            dated += 1
            if when.astimezone(US_TZ).date() != today:
                continue
            url = e["url"]
            if not url or url in seen_urls or not e["title"]:
                continue
            if not is_entertainment(host, e["title"], e["tags"], url):
                continue
            seen_urls.add(url)
            low = f"{e['title']} {e['summary']}".lower()
            hit = next((t for t in trends if len(t) > 3 and t in low), None)
            items.append({"title": e["title"], "url": url, "source": host,
                          "category": classify(f"{e['title']} {e['tags']} {url}"),
                          "image": e["image"], "published": when.isoformat(),
                          "trending": bool(hit), "trend_term": hit})
            kept += 1
        status[host] = kept
        print(f"{host}: source={used} entries={len(entries)} with_date={dated} kept_today={kept}")
    items.sort(key=lambda i: i["published"], reverse=True)
    items.sort(key=lambda i: not i["trending"])
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "date_us": str(today), "per_site": status, "items": items}
    json.dump(out, open("news.json", "w"), ensure_ascii=False, indent=1)
    print("saved", len(items), "stories")

if __name__ == "__main__":
    main()
