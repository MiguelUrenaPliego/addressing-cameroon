"""Render each topic's content.md into its index.html using the shared
site template. Run this after editing any content.md.

Usage:
    python3 reports/project_web/build_pages.py
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown

FIGURE_PARA_RE = re.compile(
    r'<p><img alt="([^"]*)" src="([^"]+)"(?: title="([^"]*)")? ?/></p>'
)


def wrap_standalone_images_as_figures(html: str) -> str:
    def repl(m: re.Match) -> str:
        alt, src, title = m.group(1), m.group(2), m.group(3)
        caption = f"<figcaption>{title}</figcaption>" if title else ""
        return f'<figure><img alt="{alt}" src="{src}">{caption}</figure>'

    return FIGURE_PARA_RE.sub(repl, html)


def wrap_references_section(html: str) -> str:
    """Wrap the rendered <h2>References</h2> and everything after it in a
    styled <div class="references">, applied after markdown rendering so
    the heading/list themselves still go through normal markdown parsing
    (a raw <div> in the source would make python-markdown skip parsing
    everything inside it as an HTML block)."""
    marker = "<h2>References</h2>"
    idx = html.find(marker)
    if idx == -1:
        return html
    return f'{html[:idx]}<div class="references">\n{html[idx:]}\n</div>'

ROOT = Path(__file__).resolve().parent

SITE_NAME = "Low-Cost Geospatial Infrastructure for Community-Driven Urban Development in Cameroon"

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="../style.css">
</head>
<body>
<header class="site">
  <h1>{title}</h1>
  <p class="subtitle">{subtitle}</p>
</header>
<nav class="crumbs"><a href="../../../index.html">Home</a> / {title}</nav>

<main class="article">
{content}
</main>

<div class="map-embed">
  <h2 style="text-align:center; font-family: sans-serif; font-weight: 600; color: var(--muted); font-size: 0.95rem; letter-spacing: 0.03em; text-transform: uppercase;">Interactive map</h2>
  <iframe src="{map_src}" loading="lazy"></iframe>
  <p class="caption">Use the dropdown inside the map to switch between areas or drives.</p>
</div>

<footer class="site">{site_name}</footer>
</body>
</html>
"""

TOPICS = [
    {
        "dir": "imu-road-quality",
        "title": "IMU Road Quality",
        "subtitle": "Road-surface classification from smartphone accelerometer and GPS data",
        "map_src": "../maps/imu/index.html",
    },
    {
        "dir": "addressing-system",
        "title": "Addressing System",
        "subtitle": "A community-driven street-addressing pipeline built on OpenStreetMap",
        "map_src": "../../../code/OSMAddressSystem/experiments/index.html",
    },
    {
        "dir": "proximity",
        "title": "Proximity",
        "subtitle": "15-minute-city accessibility analysis from building-level survey data",
        "map_src": "../maps/proximity/index.html",
    },
    {
        "dir": "building-survey",
        "title": "Building Survey",
        "subtitle": "Initial RTK/GPS building survey data and the plan for a full Fotouni census",
        "map_src": "../../../data/survey_maps_index.html",
    },
]


def main() -> None:
    for topic in TOPICS:
        topic_dir = ROOT / topic["dir"]
        md_path = topic_dir / "content.md"
        html_body = markdown.markdown(
            md_path.read_text(), extensions=["tables", "attr_list"]
        )
        html_body = wrap_standalone_images_as_figures(html_body)
        html_body = wrap_references_section(html_body)
        page = PAGE_TEMPLATE.format(
            title=topic["title"],
            subtitle=topic["subtitle"],
            content=html_body,
            map_src=topic["map_src"],
            site_name=SITE_NAME,
        )
        out_path = topic_dir / "index.html"
        out_path.write_text(page)
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
