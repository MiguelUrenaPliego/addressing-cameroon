"""Build proximity/index.html: a single page combining every site's
proximity/accessibility map into one dropdown-selectable viewer.

Scans ``proximity/*/map.html`` at run time, so any new site folder (e.g.
``proximity/<NewSite>/map.html``) is picked up automatically the next time
this script is run - no hardcoded list of sites to maintain.

Usage:
    uv run proximity/build_index.py
"""

from __future__ import annotations

import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "index.html"


def find_maps() -> list[tuple[str, str]]:
    """Return [(site_name, relative_path), ...], sorted by site name."""
    maps = []
    for map_path in sorted(ROOT.glob("*/map.html")):
        site_name = map_path.parent.name
        rel_path = map_path.relative_to(ROOT).as_posix()
        maps.append((site_name, rel_path))
    return maps


def build_html(maps: list[tuple[str, str]]) -> str:
    options = []
    first_path = None
    default_path = None
    for site_name, rel_path in maps:
        if first_path is None:
            first_path = rel_path
        if site_name == "fotouni":
            default_path = rel_path
        selected = ' selected' if site_name == "fotouni" else ''
        options.append(f'  <option value="{html.escape(rel_path)}"{selected}>{html.escape(site_name)}</option>')
    options_html = "\n".join(options)
    # Fotouni is the site we want the page to open on by default, when present,
    # regardless of where it falls alphabetically among the scanned sites.
    default_path = default_path or first_path

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Proximity — Sites</title>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; font-family: sans-serif; }}
  #bar {{
    position: fixed; top: 0; left: 50%; transform: translateX(-50%);
    z-index: 1000; background: white; padding: 8px 16px; border-radius: 0 0 8px 8px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.4); text-align: center;
  }}
  #bar label {{ font-size: 13px; margin-right: 8px; }}
  #bar select {{ font-size: 14px; padding: 4px 8px; }}
  #viewer {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; border: none; }}
</style>
</head>
<body>
<div id="bar">
  <label for="map-select">Site:</label>
  <select id="map-select">
{options_html}
  </select>
</div>
<iframe id="viewer" src="{html.escape(default_path or "")}"></iframe>
<script>
  const select = document.getElementById("map-select");
  const viewer = document.getElementById("viewer");
  select.addEventListener("change", () => {{
    viewer.src = select.value;
  }});
</script>
</body>
</html>
"""


def main() -> None:
    maps = find_maps()
    if not maps:
        print(f"No map.html files found under {ROOT}")
        return
    INDEX_PATH.write_text(build_html(maps))
    print(f"Wrote {INDEX_PATH} with {len(maps)} site map(s)")


if __name__ == "__main__":
    main()
