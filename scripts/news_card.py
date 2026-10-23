#!/usr/bin/env python3
"""
Silent "read it yourself" news card for trending Shorts (no voice-over).

Look: a plain card on a USA-flag background, tinted by the story's mood
  green = good news, red = bad news, blue = neutral
with a rainbow-coloured category tag (7 categories = 7 rainbow colours).
Layout stays inside YouTube's Shorts safe area (the right-hand buttons and
the bottom title bar cover the edges), same idea as the Mahanavi muhurat cards.
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920

# One rainbow colour per category (tech and gadgets share blue).
RAINBOW = {
    "trending": ("#E53935", "#FFFFFF"),   # red
    "news":     ("#FB8C00", "#1A1A1A"),   # orange
    "money":    ("#FDD835", "#1A1A1A"),   # yellow
    "science":  ("#43A047", "#FFFFFF"),   # green
    "tech":     ("#1E88E5", "#FFFFFF"),   # blue
    "gadgets":  ("#1E88E5", "#FFFFFF"),   # blue
    "movies":   ("#3949AB", "#FFFFFF"),   # indigo
    "upcoming": ("#8E24AA", "#FFFFFF"),   # violet
}
MOODS = {
    "good":    {"tint": (16, 120, 56),  "label": "GOOD NEWS",  "chip": "#2E9E57"},
    "bad":     {"tint": (170, 24, 32),  "label": "BAD NEWS",   "chip": "#C62828"},
    "neutral": {"tint": (20, 52, 120),  "label": "IN THE NEWS", "chip": "#1F4E9E"},
}
CATEGORY_LABEL = {
    "trending": "TRENDING NOW", "news": "US NEWS", "money": "MONEY NEWS", "science": "SCIENCE AND SPACE",
    "tech": "TECH NEWS", "gadgets": "GADGETS", "movies": "MOVIE NEWS", "upcoming": "COMING UP",
}


def _font(path, size):
    return ImageFont.truetype(str(path), size)


def _star(draw, cx, cy, r, fill):
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.382
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=fill)


def flag(w=W, h=H):
    """A US flag (13 stripes, canton with 50 stars) scaled to fill the frame,
    rotated so the stripes run horizontally behind the text."""
    fw, fh = 1900, 1000  # flag ratio 1.9
    img = Image.new("RGB", (fw, fh), "#FFFFFF")
    d = ImageDraw.Draw(img)
    sh = fh / 13
    for i in range(13):
        if i % 2 == 0:
            d.rectangle([0, round(i * sh), fw, round((i + 1) * sh)], fill="#B22234")
    cw, ch = round(fw * 0.4), round(sh * 7)
    d.rectangle([0, 0, cw, ch], fill="#3C3B6E")
    for row in range(9):
        n = 6 if row % 2 == 0 else 5
        for k in range(n):
            x = cw * ((k * 2 + (0 if row % 2 == 0 else 1)) * 0.0905 + 0.0452)
            y = ch * (row * 0.1111 + 0.0556)
            _star(d, x, y, 17, "#FFFFFF")
    # scale to cover the portrait frame
    scale = max(w / fw, h / fh)
    img = img.resize((round(fw * scale), round(fh * scale)), Image.LANCZOS)
    left = 0  # keep the blue canton (top-left) in view
    return img.crop((left, 0, left + w, h))


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, font_path, max_w, max_h, start, minimum, spacing=1.18):
    size = start
    while size >= minimum:
        f = _font(font_path, size)
        lines = _wrap(draw, text, f, max_w)
        if len(lines) * size * spacing <= max_h:
            return f, lines, size
        size -= 4
    f = _font(font_path, minimum)
    lines = _wrap(draw, text, f, max_w)
    max_lines = max(int(max_h // (minimum * spacing)), 1)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(",;: ") + "..."
    return f, lines, minimum


def render_png(topic, font_path, out_png):
    card = topic.get("card", {})
    category = topic.get("category", "news")
    mood = MOODS[card.get("mood", "neutral")]
    accent, accent_text = RAINBOW.get(category, RAINBOW["news"])

    base = flag().convert("RGBA")
    tint = Image.new("RGBA", (W, H), mood["tint"] + (165,))
    img = Image.alpha_composite(base, tint)
    d = ImageDraw.Draw(img, "RGBA")

    # rainbow bar (category colour) across the top edge
    d.rectangle([0, 0, W, 18], fill=accent)

    # category tag + mood chip (inside the safe area, below the Shorts status bar)
    f_tag = _font(font_path, 44)
    cat_text = CATEGORY_LABEL.get(category, "NEWS")
    tw = d.textlength(cat_text, font=f_tag)
    x0, y0 = 70, 250
    d.rounded_rectangle([x0, y0, x0 + tw + 60, y0 + 92], radius=46, fill=accent, outline="#FFFFFF", width=5)
    d.text((x0 + 30, y0 + 46), cat_text, font=f_tag, fill=accent_text, anchor="lm")
    f_chip = _font(font_path, 38)
    chip = mood["label"]
    cw_ = d.textlength(chip, font=f_chip)
    cx0 = x0 + tw + 60 + 24
    d.rounded_rectangle([cx0, y0 + 6, cx0 + cw_ + 50, y0 + 86], radius=40, fill="#FFFFFF")
    d.text((cx0 + 25, y0 + 46), chip, font=f_chip, fill=mood["chip"], anchor="lm")

    # Lay out the text first, then size the dark reading panel to fit it
    # and centre it between the tags and the footer.
    px0, px1 = 50, W - 140
    inner_w = px1 - px0 - 90
    area_top, area_bottom = 400, 1390
    blocks = []  # (text, font, fill, line_height, extra)
    kicker, summary = card.get("kicker", ""), card.get("summary", "")
    if kicker:
        f_k = _font(font_path, 40)
        blocks += [(ln, f_k, "#FFE082", 54, {}) for ln in _wrap(d, kicker, f_k, inner_w)[:2]]
    max_text_h = area_bottom - area_top - 190
    head_h = max_text_h * (0.58 if summary else 1.0)
    f_h, h_lines, h_size = _fit(d, topic["title"], font_path, inner_w, head_h, 84, 52)
    blocks += [(ln, f_h, "#FFFFFF", round(h_size * 1.18), {"stroke": True}) for ln in h_lines]
    if summary:
        used = sum(b[3] for b in blocks) + 90
        f_s, s_lines, s_size = _fit(d, summary, font_path, max(max_text_h - used, 200), max(max_text_h - used, 200), 46, 32, 1.28) \
            if False else _fit(d, summary, font_path, inner_w, max(max_text_h - used, 200), 46, 32, 1.28)
        blocks += [(ln, f_s, "#F2F2F2", round(s_size * 1.28), {"first_summary": i == 0}) for i, ln in enumerate(s_lines)]
    text_h = sum(b[3] for b in blocks) + (90 if summary else 0) + (22 if kicker else 0)
    panel_h = max(text_h + 50 + 110, 640)
    py0 = area_top + (area_bottom - area_top - panel_h) // 2
    py1 = py0 + panel_h
    d.rounded_rectangle([px0, py0, px1, py1], radius=36, fill=(0, 0, 0, 165))
    y = py0 + 50 + (panel_h - 160 - text_h) // 2
    for i, (ln, f, fill, lh, extra) in enumerate(blocks):
        if kicker and i == 1 - (0 if len(_wrap(d, kicker, _font(font_path, 40), inner_w)) < 2 else -1) and False:
            pass
        if extra.get("first_summary"):
            y += 34
            d.rectangle([px0 + 45, y, px0 + 45 + 120, y + 8], fill=accent)
            y += 40
        kw = {"stroke_width": 2, "stroke_fill": "#000000"} if extra.get("stroke") else {}
        d.text((px0 + 45, y), ln, font=f, fill=fill, **kw)
        y += lh
        if kicker and f is f_k and (i + 1 >= len(blocks) or blocks[i + 1][1] is not f_k):
            y += 22
    pub = topic.get("source_publisher", "")
    if pub:
        f_src = _font(font_path, 36)
        d.text((px0 + 45, py1 - 30), f"Source: {pub}", font=f_src, fill="#CFD8DC", anchor="ls")

    # follow line (below the panel, above the Shorts title bar)
    f_f = _font(font_path, 40)
    d.text((W / 2 - 45, 1450), "Daily US news and money updates", font=f_f, fill="#FFFFFF", anchor="mm")
    d.text((W / 2 - 45, 1510), "TaxSaveUSA  |  Subscribe", font=f_f, fill="#FFE082", anchor="mm")

    img.convert("RGB").save(out_png, quality=95)
    return out_png


def reading_seconds(topic):
    card = topic.get("card", {})
    words = len((topic["title"] + " " + card.get("summary", "") + " " + card.get("kicker", "")).split())
    return max(9.0, min(14.0, 5.0 + words / 3.2))


def render_video(topic, font_path, out_mp4, work_dir):
    work_dir.mkdir(parents=True, exist_ok=True)
    png = render_png(topic, font_path, work_dir / f"{topic['slug']}.png")
    dur = reading_seconds(topic)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(out_mp4),
    ], check=True)
    return dur
