#!/usr/bin/env python3
"""
Picks a currently trending US story and turns it into a "topic" dict that
the existing video pipeline (generate_video.py) can render unchanged.

Sources (all free, no API key, no billing):
  - Google Trends "Trending now" RSS for the US (search terms + the news
    headlines behind them)
  - Public RSS feeds from NPR, BBC, ESPN, CBS Sports, Ars Technica for
    news / tech / sports / entertainment / money

What this does NOT do: it never invents facts. The spoken script is the
publisher's own headline plus (when the feed provides one) a short
summary sentence from that publisher's feed, always credited to the
publisher by name, with a link to the original in the description. There
is no LLM involved (no billing), so wording stays close to the source.

Safety filter: trending lists are full of tragedies (shootings, deaths,
overdoses) and campaign politics. Those are skipped by BLOCK_PATTERN so
the channel stays advertiser-friendly and does not turn real people's
worst days into upbeat "congratulations, you learned something" Shorts.
Edit BLOCK_WORDS below to loosen or tighten it.

Dedup: state/trending_state.json remembers recent headlines so the same
story is not posted twice, and rotates categories so consecutive Shorts
are not all the same topic.
"""
import hashlib
import html
import json
import random
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "trending_state.json"
BACKGROUNDS = ["default.mp4", "bg2.mp4", "bg3.mp4", "bg4.mp4"]

HT_NS = "{https://trends.google.com/trending/rss}"
MAX_AGE_HOURS = 36
SEEN_LIMIT = 500
TAX_EVERY = 6  # in "auto" mode, every 6th Short is a tax/money tip from topics.json

# (category, on-screen hook, playlist title, intro line, [(feed url, publisher), ...])
CATEGORIES = [
    ("trending", "TRENDING NOW", "Trending Now in the US",
     "Here's what's trending in the US right now.", []),  # fed by Google Trends below
    ("news", "US NEWS", "US News Today",
     "Here's a top US news headline.",
     [("https://feeds.npr.org/1001/rss.xml", "NPR"),
      ("https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "BBC News")]),
    ("tech", "TECH NEWS", "Tech News",
     "Here's a trending tech story.",
     [("https://feeds.bbci.co.uk/news/technology/rss.xml", "BBC News"),
      ("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
      ("https://feeds.npr.org/1019/rss.xml", "NPR")]),
    ("gadgets", "GADGETS", "Gadgets and Tech Reviews",
     "Here's a trending gadget story.",
     [("https://www.theverge.com/rss/index.xml", "The Verge"),
      ("https://www.engadget.com/rss.xml", "Engadget"),
      ("https://www.cnet.com/rss/news/", "CNET")]),
    ("science", "SCIENCE AND SPACE", "Science and Space News",
     "Here's a trending science story.",
     [("https://feeds.npr.org/1007/rss.xml", "NPR"),
      ("https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "BBC News"),
      ("https://www.nasa.gov/feed/", "NASA")]),
    ("money", "MONEY NEWS", "Money and Business News",
     "Here's a money and business headline.",
     [("https://feeds.npr.org/1006/rss.xml", "NPR"),
      ("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC News")]),
    ("upcoming", "COMING UP", "Coming Up: Dates to Know",
     "Here's a date coming up that matters.", []),  # fed by content/upcoming.json
]
TRENDS_URL = "https://trends.google.com/trending/rss?geo=US"
CATEGORY_TAGS = {
    "trending": ["trending", "trending now", "trending in usa", "viral", "google trends"],
    "news": ["us news", "news today", "breaking news", "headlines", "usa news"],
    "tech": ["tech news", "technology", "gadgets", "ai news", "tech today"],
    "sports": ["sports news", "sports highlights", "nfl", "nba", "sports today"],
    "entertainment": ["entertainment news", "celebrity news", "movies", "tv shows", "pop culture"],
    "money": ["business news", "economy", "stock market", "money news", "finance news"],
    "gadgets": ["gadgets", "tech reviews", "new gadgets", "smartphones", "tech gadgets 2026"],
    "science": ["science news", "space news", "nasa", "science explained", "discoveries"],
    "upcoming": ["upcoming", "what's coming", "dates to know", "important dates", "week ahead"],
}
HASHTAG_BY_CATEGORY = {"trending": "Viral", "news": "BreakingNews", "tech": "TechNews", "sports": "SportsNews",
                       "entertainment": "CelebNews", "money": "BusinessNews", "gadgets": "Gadgets",
                       "science": "ScienceNews", "upcoming": "ComingUp"}
PLAYLIST_DESCRIPTIONS = {
    "Trending Now in the US": "What Americans are searching for right now. New Shorts every day. Trending news in the USA explained in 30 seconds.",
    "US News Today": "Top US news headlines in under a minute, always credited to the publisher. Daily US news Shorts.",
    "Tech News": "Technology news from the US and the world: AI, apps, software, the internet. Daily tech news Shorts.",
    "Gadgets and Tech Reviews": "New gadgets, phones, laptops and smart devices. Quick daily gadget news Shorts.",
    "Science and Space News": "Science discoveries, NASA and space news explained simply. Daily science Shorts.",
    "Money and Business News": "Money, markets, jobs and prices that affect your wallet. Daily money news Shorts.",
    "Coming Up: Dates to Know": "Upcoming deadlines, Fed meetings, tax dates and events that matter. Know it before it happens.",
}

BASE_TAGS = ["shorts", "youtube shorts", "personal finance", "money news", "economy", "usa", "finance news", "money tips", "explained"]

# Skip anything touching these - tragedies, graphic or adult content, and
# campaign politics. Matched on word starts, case-insensitive. A trailing
# space means "whole word only" (so "war " blocks "war" but not "Warriors",
# and "dead " blocks "dead" but not "deadline").
BLOCK_WORDS = [
    "kill", "dead ", "deadly", "death", "dies", "died", "dying", "fatal", "shoot", "gunman", "gunfire",
    "murder", "homicide", "suicide", "rape", "sexual", "assault", "abuse", "molest", "terror",
    "attack", "massacre", "bomb", "stabb", "hostage", "kidnap", "overdose", "victim", "war ", "wars ",
    "crash", "drown", "injur", "wound", "missing", "casualt", "porn", "nude", "explicit",
    "killer", "suspect", "arrest", "indict", "trial", "lawsuit", "sued", "prison", "jail",
    "election", "campaign", "ballot", "candidate", "impeach", "partisan", "gop", "democrat",
    "republican", "israel", "gaza", "hamas", "ukraine", "russia", "abortion", "tragedy",
    "funeral", "obituary", "mourn", "cancer", "outbreak", "epidemic", "hospitalized",
]
BLOCK_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(w.strip()) + (r"\b" if w.endswith(" ") else "") for w in BLOCK_WORDS) + r")",
    re.I,
)


# Money focus: finance advertisers pay several times more per view than
# sports or celebrity news, and a tight niche is what the recommendation
# system needs to learn who to show the channel to. When FINANCE_ONLY is
# True a story must mention at least one of these topics to be used.
FINANCE_ONLY = True
FINANCE_STRICT = {"money"}   # only the money category must mention a finance topic; the other pillars are open
FINANCE_WORDS = [
    "tax", "irs", "refund", "social security", "medicare", "medicaid", "retire", "401k", "401(k)", "ira ",
    "pension", "inflation", "interest rate", "mortgage", "housing", "home price", "rent ", "rents", "federal reserve",
    "fed ", "jobs report", "unemploy", "layoff", "wage", "salary", "paycheck", "minimum wage", "gas price",
    "grocery", "prices", "tariff", "economy", "economic", "recession", "stock", "stocks", "dow ", "nasdaq", "s&p",
    "wall street", "bank", "credit", "debt", "loan", "savings", "insurance", "benefit", "stimulus", "budget",
    "treasury", "gdp", "earnings", "consumer", "shopping", "cost of living", "deduction", "bonus", "payroll",
    "student loan", "scam", "fraud", "fee", "bills", "utility", "electric bill", "health care costs",
]
FINANCE_PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(w.strip()) for w in FINANCE_WORDS) + r")", re.I)


def _on_topic(*texts, category=""):
    if not FINANCE_ONLY or category not in FINANCE_STRICT:
        return True
    return bool(FINANCE_PATTERN.search(" ".join(texts)))


class NoTrendingAvailable(Exception):
    pass


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, json.JSONDecodeError):
        st = {}
    st.setdefault("count", 0)
    st.setdefault("seen", [])
    st.setdefault("recent_categories", [])
    return st


def save_state(st):
    st["seen"] = st["seen"][-SEEN_LIMIT:]
    st["recent_categories"] = st["recent_categories"][-6:]
    STATE_PATH.parent.mkdir(exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)


# --------------------------------------------------------------- helpers

def _norm(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _tokens(text):
    return {w for w in _norm(text).split() if len(w) > 3}


def _is_dup(title, seen):
    key = hashlib.sha1(_norm(title).encode()).hexdigest()[:12]
    toks = _tokens(title)
    for item in seen[-80:]:
        if item["key"] == key:
            return True
        other = set(item.get("tokens", []))
        if toks and other and len(toks & other) / len(toks | other) >= 0.6:
            return True
    return False


def _clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("&", " and ").replace("…", ".").replace("...", ".")
    text = re.sub(r"[^\x20-\x7E]", "", text)  # drop emoji / odd glyphs that break TTS and fonts
    return re.sub(r"\s+", " ", text).strip()


def _first_sentence(text, max_words=26):
    text = _clean(text)
    if not text:
        return ""
    m = re.match(r"(.+?[.!?])(?:\s|$)", text)
    sentence = m.group(1) if m else text
    words = sentence.split()
    if len(words) > max_words:
        sentence = " ".join(words[:max_words]).rstrip(",;:") + "."
    return sentence


def _strip_publisher(title, publisher=None):
    title = _clean(title)
    return re.sub(r"\s+[-|]\s+[^-|]{2,40}$", "", title) if publisher else title


def _sentence(text):
    text = text.strip()
    return text if text[-1:] in ".!?" else text + "."


def _parse_date(value):
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _fresh(dt, now):
    return dt is None or (now - dt) <= timedelta(hours=MAX_AGE_HOURS)


# --------------------------------------------------------------- parsing

def parse_rss_items(xml_text, publisher):
    """Standard RSS 2.0 -> [{title, summary, link, published, publisher}]"""
    items = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = _clean(it.findtext("title"))
        if not title:
            continue
        items.append({
            "title": title,
            "summary": _first_sentence(it.findtext("description")),
            "link": (it.findtext("link") or "").strip(),
            "published": _parse_date(it.findtext("pubDate")),
            "publisher": publisher,
        })
    return items


def parse_trends(xml_text):
    """Google Trends RSS -> [{term, traffic, headline, publisher, link, published}]"""
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        term = _clean(it.findtext("title"))
        traffic = _clean(it.findtext(HT_NS + "approx_traffic"))
        news = []
        for n in it.findall(HT_NS + "news_item"):
            news.append({
                "title": _clean(n.findtext(HT_NS + "news_item_title")),
                "publisher": _clean(n.findtext(HT_NS + "news_item_source")),
                "link": (n.findtext(HT_NS + "news_item_url") or "").strip(),
            })
        if term and news:
            out.append({"term": term, "traffic": traffic, "news": news,
                        "published": _parse_date(it.findtext("pubDate"))})
    return out


def fetch(url):
    import requests
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (TaxSaveUSA trending bot)"})
    resp.raise_for_status()
    return resp.content


# ------------------------------------------------------------- topic build

def _traffic_phrase(traffic):
    m = re.match(r"(\d[\d,]*)\+?", traffic or "")
    if not m:
        return ""
    n = int(m.group(1).replace(",", ""))
    return f" with over {n:,} searches" if n >= 1000 else ""


def _hashtags(words):
    return " ".join("#" + re.sub(r"\W", "", w) for w in words)


def _build_topic(category, hook, playlist, script, title, link, publisher, headline, rng):
    cat_tags = CATEGORY_TAGS[category]
    title_words = [w for w in re.findall(r"[A-Za-z][A-Za-z']{3,}", headline) if w[0].isupper()][:4]
    tags = [w.lower() for w in title_words] + cat_tags
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    slug = f"trend-{stamp}-{hashlib.sha1(headline.encode()).hexdigest()[:6]}"
    description = (
        f"{headline} (reported by {publisher}).\n\n"
        f"Quick summary of a trending US story. Read the original report for full details"
        f"{': ' + link if link else ''}\n\n"
        f"General information only, not advice. Headlines can change as stories develop.\n\n"
        + _hashtags(["TrendingNow", "USNews", HASHTAG_BY_CATEGORY[category], "Today"])
    )
    return {
        "slug": slug,
        "title": (headline if len(headline) <= 92 else headline[:89].rstrip() + "...") ,
        "caption_headline": hook,
        "background": rng.choice(BACKGROUNDS),
        "tags": tags,
        "base_tags": BASE_TAGS,
        "script": script,
        "description": description,
        "category": category,
        "playlist": playlist,
        "pre_condensed": True,
        "source_link": link,
        "source_publisher": publisher,
    }


def _candidates_for(category, now):
    """Returns a list of (headline, script_core, link, publisher) for a category, newest first."""
    cat = next(c for c in CATEGORIES if c[0] == category)
    _, _, _, intro, feeds = cat
    out = []
    if category == "upcoming":
        return upcoming_candidates(now)
    if category == "trending":
        try:
            trends = parse_trends(fetch(TRENDS_URL))
        except Exception as exc:  # network / parse - treat as "no candidates", caller falls through
            print(f"trending: Google Trends fetch failed: {exc}", file=sys.stderr)
            return out
        for t in trends:
            if not _fresh(t["published"], now):
                continue
            # Judge the term AND every attached headline: if ANY of them is a
            # tragedy, the whole trend is skipped (a neutral-looking term like
            # "oregon" can be trending because of one).
            blob = " ".join([t["term"]] + [n["title"] for n in t["news"]])
            if BLOCK_PATTERN.search(blob):
                continue
            if not _on_topic(blob, category=category):
                continue
            n = t["news"][0]
            headline = _strip_publisher(n["title"])
            term = t["term"][:1].upper() + t["term"][1:]
            core = (f"{term} is trending across the US right now{_traffic_phrase(t['traffic'])}. "
                    f"{_sentence(headline)} That's according to {n['publisher'] or 'news reports'}.")
            out.append((headline, core, n["link"], n["publisher"] or "news reports"))
        return out

    for url, publisher in feeds:
        try:
            items = parse_rss_items(fetch(url), publisher)
        except Exception as exc:
            print(f"trending: feed failed {url}: {exc}", file=sys.stderr)
            continue
        for it in items:
            if not _fresh(it["published"], now):
                continue
            if BLOCK_PATTERN.search(it["title"] + " " + it["summary"]):
                continue
            if not _on_topic(it["title"], it["summary"], category=category):
                continue
            headline = it["title"]
            core = f"{intro} {_sentence(headline)}"
            if it["summary"] and _norm(it["summary"]) not in _norm(headline) and _norm(headline) not in _norm(it["summary"]):
                core += f" {it['summary']}"
            core += f" That's according to {publisher}."
            out.append((headline, core, it["link"], publisher))
    return out


UPCOMING_PATH = ROOT / "content" / "upcoming.json"


def upcoming_candidates(now, horizon_days=21):
    """Dates from content/upcoming.json that fall in the next `horizon_days`, soonest first:
    [(headline, script_core, link, publisher)]. Curated from official sources (IRS, Fed, CMS, NASA)."""
    try:
        events = json.loads(UPCOMING_PATH.read_text(encoding="utf-8"))["events"]
    except (OSError, ValueError, KeyError):
        return []
    today = now.date()
    out = []
    for ev in sorted(events, key=lambda e: e["date"]):
        d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        days = (d - today).days
        if not 0 <= days <= horizon_days:
            continue
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days, on {d.strftime('%B')} {d.day}"
        headline = f"{ev['title']} ({d.strftime('%B')} {d.day})"
        core = (f"Coming up {when}: {ev['title']}. {ev['say']} That's according to {ev['source']}.")
        out.append((headline, core, ev.get("link", ""), ev["source"]))
    return out


def pick_trending_topic(seed=None):
    """Chooses the next trending story, records it in state, returns (topic, state_count)."""
    rng = random.Random(seed if seed is not None else datetime.now(timezone.utc).isoformat())
    now = datetime.now(timezone.utc)
    st = load_state()
    recent = st["recent_categories"][-3:]
    order = [c[0] for c in CATEGORIES]
    rng.shuffle(order)
    order.sort(key=lambda c: c in recent)  # categories not used lately go first

    for category in order:
        cat = next(c for c in CATEGORIES if c[0] == category)
        for headline, core, link, publisher in _candidates_for(category, now):
            if _is_dup(headline, st["seen"]):
                continue
            topic = _build_topic(category, cat[1], cat[2], core, headline, link, publisher, headline, rng)
            count_before = st["count"]
            st["seen"].append({"key": hashlib.sha1(_norm(headline).encode()).hexdigest()[:12],
                               "tokens": sorted(_tokens(headline)), "t": now.isoformat(), "category": category})
            st["recent_categories"].append(category)
            st["count"] = count_before + 1
            save_state(st)
            return topic, count_before
    raise NoTrendingAvailable("no fresh, safe, unseen trending story found in any category")


def due_for_tax_tip():
    """In auto mode every TAX_EVERY-th Short is a tax tip (keeps the channel's original niche alive)."""
    st = load_state()
    return st["count"] % TAX_EVERY == TAX_EVERY - 1


def bump_count():
    st = load_state()
    st["count"] += 1
    save_state(st)


if __name__ == "__main__":
    topic, _ = pick_trending_topic()
    print(json.dumps({k: v for k, v in topic.items() if k != "description"}, indent=2))
