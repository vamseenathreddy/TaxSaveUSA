#!/usr/bin/env python3
"""
Builds the optional "resources" footer added to video descriptions (and
the website footer) from config/monetization.json. Returns "" when nothing
is configured, so it is safe to call everywhere. Any affiliate link comes
with the disclosure line right next to it (FTC and YouTube policy).
"""
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "monetization.json"


def _cfg():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def footer() -> str:
    c = _cfg()
    lines = []
    if c.get("newsletter_url"):
        lines.append(f"{c.get('lead_magnet_text') or 'Free tax checklist'}: {c['newsletter_url']}")
    if c.get("site_live") and c.get("site_url"):
        lines.append(f"Daily money and tax briefs: {c['site_url']}")
    links = [a for a in c.get("affiliate_links", []) if a.get("url")]
    for a in links:
        lines.append(f"{a['label']}: {a['url']}")
    if links:
        lines.append(c.get("disclosure", "Some links are affiliate links."))
    return "\n".join(lines)


def append_to(description: str) -> str:
    f = footer()
    return f"{description.rstrip()}\n\n{f}" if f else description


if __name__ == "__main__":
    print(footer() or "(nothing configured)")
