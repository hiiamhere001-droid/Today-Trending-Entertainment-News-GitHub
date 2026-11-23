"""Collect today's US entertainment stories, match Google Trends (US), write news.json.
Sites with an RSS feed use it. Sites without one (Yahoo, Jang English) are scraped:
the script reads the listing page, opens each article and reads its publish time.
Install: pip install feedparser requests beautifulsoup4
"""
import json, re, time, datetime as dt
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import feedparser, requests
import xml.etree.ElementTree as ET
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
KEEP_HOURS = 48   # how long a story stays in news.json (24h main page + 24h "Lost news")

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
STOP = set("""the and but for from with that this these those his her their our your you they new newest
first last after before about over into out than then just now today tonight latest says say said reveals
reveal shares share show shows see watch here what why how who when where will would could should can may
might has have had does did get gets got more most all every any one two three star stars celebrity celebs
news big full inside everything know need things thing look looks back set sets takes take make makes made
going gone amid against via per""".split())

def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()

def toks(text):
    return {w for w in norm(text).split() if len(w) >= 3 and w not in STOP}

def overlap(a, b):
    """Two headlines are about the same story if they share 3+ key words (and half of the shorter one)."""
    if not a or not b:
        return False
    s = len(a & b)
    return s >= 3 and s / min(len(a), len(b)) >= 0.5

def traffic_num(txt):
    m = re.match(r"\s*(\d[\d,]*)\s*([KkMm]?)", txt or "")
    if not m:
        return 0
    n = int(m.group(1).replace(",", ""))
    return n * {"": 1, "k": 1000, "m": 1000000}[m.group(2).lower()]

def trends_us():
    """Google Trends (US): each trend has a term, approx. traffic and related news headlines."""
    r = get("https://trends.google.com/trending/rss?geo=US")
    out = []
    if not r:
        return out
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return out
    for item in root.iter("item"):
        term, traffic, news = "", 0, []
        for ch in item:
            tag = ch.tag.split("}")[-1]
            if tag == "title":
                term = (ch.text or "").strip()
            elif tag == "approx_traffic":
                traffic = traffic_num(ch.text)
            elif tag == "news_item":
                for n in ch:
                    if n.tag.split("}")[-1] == "news_item_title" and n.text:
                        news.append(toks(n.text))
        if term:
            out.append({"term": term, "traffic": traffic, "low": norm(term),
                        "tokens": toks(term), "news": news})
    return out

def match_trend(text, tset, trends):
    """Best Google trend for a story: phrase match, all key words present, or same headline as a trend's news."""
    best = None
    padded = f" {text} "
    for t in trends:
        hit = False
        if len(t["low"]) > 3 and f" {t['low']} " in padded:
            hit = True
        elif len(t["tokens"]) >= 2 and t["tokens"] <= tset:
            hit = True
        elif len(t["tokens"]) == 1 and len(next(iter(t["tokens"]))) >= 5 and t["tokens"] <= tset:
            hit = True
        elif any(overlap(tset, n) for n in t["news"]):
            hit = True
        if hit and (best is None or t["traffic"] > best["traffic"]):
            best = t
    return best

def fam(source):
    return "yahoo.com" if source.endswith("yahoo.com") else source

def add_buzz(items):
    """it['buzz'] = number of different sites covering the same story."""
    sets = [toks(i["title"]) for i in items]
    for a, it in enumerate(items):
        srcs = {fam(it["source"])}
        for b, o in enumerate(items):
            if a != b and overlap(sets[a], sets[b]):
                srcs.add(fam(o["source"]))
        it["buzz"] = len(srcs)

def norm_url(u):
    q = urlparse(u or "")
    return (q.netloc.lower().replace("www.", "") + q.path.rstrip("/")).lower()

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
    now = dt.datetime.now(dt.timezone.utc)
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
            # Rolling last 48 hours.
            if now - when > dt.timedelta(hours=KEEP_HOURS) or when > now + dt.timedelta(hours=1):
                continue
            url = e["url"]
            if not url or norm_url(url) in seen_urls or not e["title"]:
                continue
            if not is_entertainment(host, e["title"], e["tags"], url):
                continue
            seen_urls.add(norm_url(url))
            items.append({"title": e["title"], "url": url, "source": host,
                          "category": classify(f"{e['title']} {e['tags']} {url}"),
                          "image": e["image"], "published": when.isoformat(),
                          "trending": False, "trend_term": None, "_s": e["summary"]})
            kept += 1
        status[host] = kept
        print(f"{host}: source={used} entries={len(entries)} with_date={dated} kept_today={kept}")
    # Keep stories saved by earlier runs (feeds only show the newest posts), drop anything older than 48h.
    try:
        old_items = json.load(open("news.json", encoding="utf-8")).get("items", [])
    except Exception:
        old_items = []
    for o in old_items:
        w = parse_dt(o.get("published", ""))
        if not w or not o.get("url") or norm_url(o["url"]) in seen_urls:
            continue
        if now - w > dt.timedelta(hours=KEEP_HOURS):
            continue
        seen_urls.add(norm_url(o["url"]))
        items.append(o)
    # Trending = matches a Google Trends (US) topic, OR the same story is covered by 2+ of our sites.
    for it in items:
        was = (it.get("trending"), it.get("trend_term"))
        text = norm(f"{it['title']} {it.get('_s', '')}")
        t = match_trend(text, toks(text), trends)
        it["trending"], it["trend_term"], it["trend_score"] = False, None, 0
        if t:
            it["trending"], it["trend_term"], it["trend_score"] = True, t["term"], t["traffic"]
        elif was[0] and was[1] and not str(was[1]).startswith("covered by"):
            it["trending"], it["trend_term"] = True, was[1]   # was trending in an earlier run, keep it
    add_buzz(items)
    for it in items:
        n = it.pop("buzz", 1)
        if n >= 2:
            it["trend_score"] = it.get("trend_score", 0) + n * 1000
            label = f"covered by {n} sites"
            it["trend_term"] = f"{it['trend_term']} · {label}" if it["trending"] else label
            it["trending"] = True
        it.pop("_s", None)
    items.sort(key=lambda i: i["published"], reverse=True)
    items.sort(key=lambda i: -i.get("trend_score", 0))
    items.sort(key=lambda i: not i["trending"])
    print("trending stories:", sum(1 for i in items if i["trending"]))
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "date_us": str(today), "per_site": status, "items": items}
    json.dump(out, open("news.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved", len(items), "stories")

if __name__ == "__main__":
    main()
