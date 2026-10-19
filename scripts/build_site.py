#!/usr/bin/env python3
"""
Builds the static news site into docs/ (GitHub Pages: Settings > Pages >
Deploy from branch > main > /docs) from the post files in content/posts/.

Each post is one JSON file: content/posts/<slug>.json

  {
    "slug": "fed-holds-rates-steady",      # url-safe, unique
    "title": "Fed holds rates steady as inflation cools",
    "dek": "One-sentence subheadline.",
    "category": "money",                    # a key of config/site.json categories
    "published": "2026-10-18T14:05:00+00:00",
    "body": ["Lead paragraph.", "Context paragraph.", "..."],
    "source_name": "NPR",
    "source_url": "https://...",
    "tags": ["federal reserve", "interest rates"]
  }

No JavaScript, no external requests, no dependencies beyond the standard
library. Includes an RSS feed, sitemap, robots.txt, canonical URLs,
OpenGraph tags and NewsArticle JSON-LD for search discovery.

Usage: python3 scripts/build_site.py [--out docs]
"""
import argparse
import html
import json
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "content" / "posts"
CONFIG_PATH = ROOT / "config" / "site.json"

CSS = """
:root{--bg:#fbfaf7;--fg:#1b1b1b;--muted:#6a6a6a;--line:#e4e0d8;--accent:#b3261e;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#ecebe6;--muted:#9a9a94;--line:#2c2c2a;--accent:#ff6b5e;--card:#1c1c1b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.65 Georgia,'Times New Roman',serif}
a{color:inherit}
.wrap{max-width:720px;margin:0 auto;padding:0 20px}
header.site{border-bottom:3px double var(--line);padding:26px 0 14px;text-align:center}
header.site a{text-decoration:none}
.brand{font-size:34px;font-weight:700;letter-spacing:-.5px;margin:0}
.tag{color:var(--muted);font-style:italic;margin:4px 0 14px;font-size:16px}
nav.cats{display:flex;flex-wrap:wrap;gap:6px 18px;justify-content:center;font:600 13px/1 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.06em}
nav.cats a{text-decoration:none;color:var(--muted);padding:4px 0}
nav.cats a:hover,nav.cats a.on{color:var(--accent)}
.kicker{font:700 12px/1 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.08em;color:var(--accent)}
article.card{padding:22px 0;border-bottom:1px solid var(--line)}
article.card h2{font-size:25px;line-height:1.25;margin:6px 0}
article.card h2 a{text-decoration:none}
article.card h2 a:hover{text-decoration:underline}
.dek{color:var(--muted);margin:2px 0 6px}
.meta{font:13px/1.4 system-ui,sans-serif;color:var(--muted)}
h1.title{font-size:36px;line-height:1.15;margin:10px 0 8px;letter-spacing:-.5px}
.body p{margin:0 0 1.1em}
.source{border-top:1px solid var(--line);margin-top:28px;padding-top:14px;font:15px/1.5 system-ui,sans-serif;color:var(--muted)}
footer{border-top:3px double var(--line);margin-top:40px;padding:20px 0 50px;text-align:center;font:14px/1.6 system-ui,sans-serif;color:var(--muted)}
h1.page{font-size:30px;margin:28px 0 0}
@media (max-width:520px){h1.title{font-size:29px}.brand{font-size:28px}body{font-size:17px}}
"""


def money_block():
    """Newsletter and affiliate links from config/monetization.json (empty -> nothing shown)."""
    try:
        m = json.loads((ROOT / "config" / "monetization.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    parts = []
    if m.get("newsletter_url"):
        parts.append(f'<a href="{esc(m["newsletter_url"])}">{esc(m.get("lead_magnet_text") or "Get our free tax checklist")}</a>')
    links = [a for a in m.get("affiliate_links", []) if a.get("url")]
    parts += [f'<a href="{esc(a["url"])}" rel="sponsored nofollow noopener">{esc(a["label"])}</a>' for a in links]
    if not parts:
        return ""
    note = f'<br><small>{esc(m.get("disclosure", ""))}</small>' if links else ""
    return "<p>" + " &middot; ".join(parts) + note + "</p>"


def esc(s):
    return html.escape(str(s), quote=True)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def load_posts(categories):
    posts = []
    for p in sorted(POSTS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
            d["_dt"] = datetime.fromisoformat(d["published"])
            if d["_dt"].tzinfo is None:
                d["_dt"] = d["_dt"].replace(tzinfo=timezone.utc)
            for k in ("slug", "title", "category", "body"):
                if not d.get(k):
                    raise ValueError(f"missing {k}")
            if d["category"] not in categories:
                raise ValueError(f"unknown category {d['category']}")
        except Exception as exc:
            print(f"skipping {p.name}: {exc}")
            continue
        posts.append(d)
    posts.sort(key=lambda d: d["_dt"], reverse=True)
    return posts


def fmt_date(dt):
    return dt.strftime("%B %-d, %Y")


def page(cfg, title, desc, canonical, body, extra_head="", active=""):
    base = cfg["base_url"].rstrip("/")
    nav = "".join(
        f'<a href="{base}/category/{esc(k)}/"{" class=on" if k == active else ""}>{esc(v)}</a>'
        for k, v in cfg["categories"].items()
    )
    full_title = title if title == cfg["site_name"] else f'{title} | {cfg["site_name"]}'
    return f"""<!doctype html>
<html lang="{esc(cfg['language'][:2])}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canonical)}">
<link rel="alternate" type="application/rss+xml" title="{esc(cfg['site_name'])}" href="{base}/feed.xml">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:type" content="website"><meta property="og:url" content="{esc(canonical)}">
<meta property="og:site_name" content="{esc(cfg['site_name'])}">
<meta name="twitter:card" content="summary">
<link rel="stylesheet" href="{base}/style.css">
{extra_head}
</head><body>
<header class="site"><div class="wrap">
<p class="brand"><a href="{base}/">{esc(cfg['site_name'])}</a></p>
<p class="tag">{esc(cfg['tagline'])}</p>
<nav class="cats">{nav}</nav>
</div></header>
<main class="wrap">
{body}
</main>
<footer><div class="wrap">
{money_block()}
<p>Short news briefs rewritten from public reporting, with the original source linked on every story.<br>
Not financial, legal or tax advice. <a href="{base}/about/">About</a> &middot; <a href="{base}/feed.xml">RSS</a> &middot; <a href="{esc(cfg['youtube_url'])}">YouTube</a></p>
</div></footer>
</body></html>"""


def card(cfg, post):
    base = cfg["base_url"].rstrip("/")
    cat = cfg["categories"][post["category"]]
    dek = f'<p class="dek">{esc(post["dek"])}</p>' if post.get("dek") else ""
    return f"""<article class="card">
<span class="kicker">{esc(cat)}</span>
<h2><a href="{base}/posts/{esc(post['slug'])}/">{esc(post['title'])}</a></h2>
{dek}
<div class="meta">{fmt_date(post['_dt'])}</div>
</article>"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(out):
    cfg = load_config()
    base = cfg["base_url"].rstrip("/")
    posts = load_posts(cfg["categories"])

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    write(out / "style.css", CSS)
    write(out / ".nojekyll", "")
    write(out / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")

    # Front page
    front = posts[: cfg["front_page_count"]]
    body = "".join(card(cfg, p) for p in front) or "<p>No stories yet. Check back soon.</p>"
    write(out / "index.html", page(cfg, cfg["site_name"], cfg["tagline"], base + "/", body))

    # Category pages
    for key, label in cfg["categories"].items():
        items = [p for p in posts if p["category"] == key]
        body = f'<h1 class="page">{esc(label)}</h1>' + ("".join(card(cfg, p) for p in items) or "<p>No stories yet.</p>")
        write(out / "category" / key / "index.html",
              page(cfg, label, f"Latest {label} stories.", f"{base}/category/{key}/", body, active=key))

    # Post pages
    for p in posts:
        url = f"{base}/posts/{p['slug']}/"
        desc = p.get("dek") or p["body"][0][:155]
        paras = "".join(f"<p>{esc(t)}</p>" for t in p["body"])
        src = ""
        if p.get("source_url"):
            src = (f'<div class="source">Source: <a href="{esc(p["source_url"])}" rel="nofollow noopener">'
                   f'{esc(p.get("source_name") or "Original report")}</a>. This brief is our own summary of public reporting; '
                   f'read the original for full details.</div>')
        ld = json.dumps({
            "@context": "https://schema.org", "@type": "NewsArticle",
            "headline": p["title"][:110], "description": desc,
            "datePublished": p["_dt"].isoformat(),
            "dateModified": p.get("updated") or p["_dt"].isoformat(),
            "mainEntityOfPage": url, "inLanguage": cfg["language"],
            "author": {"@type": "Organization", "name": cfg["site_name"]},
            "publisher": {"@type": "Organization", "name": cfg["site_name"]},
        })
        head = f'<script type="application/ld+json">{ld}</script>'
        dek = f'<p class="dek">{esc(p["dek"])}</p>' if p.get("dek") else ""
        body = (f'<article><span class="kicker">{esc(cfg["categories"][p["category"]])}</span>'
                f'<h1 class="title">{esc(p["title"])}</h1>{dek}'
                f'<div class="meta">{fmt_date(p["_dt"])}</div>'
                f'<div class="body" style="margin-top:22px">{paras}</div>{src}</article>')
        write(out / "posts" / p["slug"] / "index.html",
              page(cfg, p["title"], desc, url, body, head, active=p["category"]))

    # About
    about = ("<h1 class='page'>About</h1>"
             f"<p>{esc(cfg['site_name'])} publishes short, plain-English briefs on what is trending in the US: "
             "news, tech, sports, entertainment, money and taxes.</p>"
             "<p>Every brief is written in our own words from publicly reported information, names its source and links to it. "
             "We skip tragedies, graphic content and campaign politics. If a story changes, we update or remove the brief.</p>"
             "<p>Nothing here is financial, legal or tax advice. For your own situation, talk to a qualified professional.</p>")
    write(out / "about" / "index.html", page(cfg, "About", "About this site.", base + "/about/", about))

    # RSS
    items = "".join(
        f"<item><title>{esc(p['title'])}</title><link>{base}/posts/{esc(p['slug'])}/</link>"
        f"<guid>{base}/posts/{esc(p['slug'])}/</guid><pubDate>{format_datetime(p['_dt'])}</pubDate>"
        f"<category>{esc(cfg['categories'][p['category']])}</category>"
        f"<description>{esc(p.get('dek') or p['body'][0])}</description></item>"
        for p in posts[:50]
    )
    write(out / "feed.xml",
          f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>{esc(cfg["site_name"])}</title>'
          f'<link>{base}/</link><description>{esc(cfg["tagline"])}</description><language>{esc(cfg["language"])}</language>{items}</channel></rss>')

    # Sitemap
    urls = [base + "/", base + "/about/"] + [f"{base}/category/{k}/" for k in cfg["categories"]] + \
           [f"{base}/posts/{p['slug']}/" for p in posts]
    write(out / "sitemap.xml",
          '<?xml version="1.0" encoding="utf-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          + "".join(f"<url><loc>{esc(u)}</loc></url>" for u in urls) + "</urlset>")
    print(f"built {len(posts)} posts into {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs"))
    ap.add_argument("--posts", default=None, help="override posts dir (testing)")
    a = ap.parse_args()
    if a.posts:
        POSTS_DIR = Path(a.posts)
    build(Path(a.out))
