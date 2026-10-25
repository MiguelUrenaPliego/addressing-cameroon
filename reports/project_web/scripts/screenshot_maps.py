"""Automatic, reproducible screenshots of interactive Leaflet/folium maps,
framed exactly to a given AOI (or trip) bounding box, for use as static
report figures.

Rather than eyeballing a manual zoom/pan in a browser, this script opens a
temporary copy of the target HTML map in headless Chromium, injects a
small script that finds the page's own Leaflet map object and calls
``fitBounds`` on it with the caller-supplied [[lat_min, lon_min],
[lat_max, lon_max]] box (expanded by a fixed padding fraction), waits long
enough in real time for the satellite/hybrid basemap tiles at that zoom
level to finish loading, and then takes a full-page screenshot.

Usage (see MAPS below for the concrete jobs this generates):
    python3 screenshot_maps.py
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIGURES = REPO_ROOT / "reports" / "FUASProjektarbeit" / "figures"

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
WINDOW_SIZE = (1600, 1150)
PAD_FRACTION = 0.08          # fraction of the bbox span added on every side
TILE_WAIT_MS = 9000          # real time to let tiles load after fitBounds
VIRTUAL_TIME_BUDGET_MS = TILE_WAIT_MS + 6000


def _bounds_from_points(lat_lon_pairs, pad_fraction=PAD_FRACTION):
    lats = [p[0] for p in lat_lon_pairs]
    lons = [p[1] for p in lat_lon_pairs]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    pad_lat = pad_fraction * (lat_max - lat_min or 0.001)
    pad_lon = pad_fraction * (lon_max - lon_min or 0.001)
    return (lat_min - pad_lat, lon_min - pad_lon, lat_max + pad_lat, lon_max + pad_lon)


def bounds_from_csv(csv_path: Path, lat_col: str, lon_col: str, pad_fraction=PAD_FRACTION):
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    pts = [(float(r[lat_col]), float(r[lon_col])) for r in rows if r.get(lat_col) and r.get(lon_col)]
    return _bounds_from_points(pts, pad_fraction)


def _inject_and_screenshot(html_path: Path, out_path: Path, bounds, extra_js: str = "",
                            tile_wait_ms: int = TILE_WAIT_MS, window_size=WINDOW_SIZE) -> None:
    """bounds = (lat_min, lon_min, lat_max, lon_max)."""
    lat_min, lon_min, lat_max, lon_max = bounds
    html = html_path.read_text(encoding="utf-8")

    script = f"""
<script>
(function() {{
  function findLeafletMap() {{
    for (var k in window) {{
      try {{ if (window[k] instanceof L.Map) return window[k]; }} catch (e) {{}}
    }}
    return null;
  }}
  window.addEventListener('load', function() {{
    setTimeout(function() {{
      var m = findLeafletMap();
      if (m) {{
        m.fitBounds([[{lat_min}, {lon_min}], [{lat_max}, {lon_max}]], {{animate: false}});
        m.invalidateSize();
      }}
      {extra_js}
    }}, 400);
  }});
}})();
</script>
</body>"""
    html = html.replace("</body>", script, 1)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, dir=html_path.parent) as tmp:
        tmp.write(html)
        tmp_path = Path(tmp.name)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            CHROMIUM, "--headless=new", "--disable-gpu",
            f"--screenshot={out_path}",
            f"--window-size={window_size[0]},{window_size[1]}",
            f"--virtual-time-budget={tile_wait_ms + 6000}",
            "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            tmp_path.resolve().as_uri(),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"Saved {out_path}")
    finally:
        tmp_path.unlink(missing_ok=True)


def screenshot_road_quality_drives() -> None:
    outputs = REPO_ROOT / "code" / "IMURoadQuality" / "experiments" / "Cameroon" / "outputs"
    drives = {
        "Fotouni_25_03_2026__2026-03-25_09-32-13": "drive_fotouni_0325_0932.jpg",
        "Fotouni_25_03_2026__2026-03-25_15-03-36": "drive_fotouni_0325_1503.jpg",
        "Fotouni_25_03_2026__2026-03-25_15-15-53": "drive_fotouni_0325_1515.jpg",
        "Fotouni_Douala_26_03_2026__2026-03-26_08-44-46": "drive_fotouni_douala_0326_0844.jpg",
        "Fotouni_Douala_26_03_2026__2026-03-26_13-16-53": "drive_fotouni_douala_0326_1316.jpg",
    }
    for drive_dir, out_name in drives.items():
        base = outputs / drive_dir
        bounds = bounds_from_csv(base / "classified_points.csv", "latitude", "longitude")
        _inject_and_screenshot(base / "road_quality_map.html", FIGURES / out_name, bounds)


def screenshot_proximity_walkscores() -> None:
    """Fotouni/Douala walk-score maps, AOI-framed, walk-score layer selected."""
    select_walk_score_js = (
        "var sel = document.getElementById('score-select'); "
        "if (sel) { sel.value = 'walk_score'; "
        "if (typeof onScoreChange === 'function') { onScoreChange(sel); } "
        "else { sel.dispatchEvent(new Event('change')); } }"
    )
    jobs = {
        "fotouni": REPO_ROOT / "code" / "proximity" / "aoi_fotouni.gpkg",
        "douala": REPO_ROOT / "code" / "proximity" / "aoi_douala.gpkg",
    }
    import geopandas as gpd  # local import: only needed for this job, uses proximity's venv
    for site, aoi_path in jobs.items():
        aoi = gpd.read_file(aoi_path).to_crs("EPSG:4326")
        minx, miny, maxx, maxy = aoi.total_bounds
        bounds = (miny, minx, maxy, maxx)
        html_path = REPO_ROOT / "code" / "proximity" / site / "map.html"
        out_path = FIGURES / f"proximity_{site}_walkscore.jpg"
        _inject_and_screenshot(html_path, out_path, bounds, extra_js=select_walk_score_js,
                                tile_wait_ms=11000)


def screenshot_addressing_survey_maps() -> None:
    """New: survey_map.html screenshots for the addressing/census pilot,
    framed to the extent of the actual surveyed buildings in each town."""
    jobs = {
        "douala": (REPO_ROOT / "data" / "douala" / "pilot_survey" / "survey_map.html",
                   REPO_ROOT / "data" / "douala" / "pilot_survey" / "douala.csv",
                   FIGURES / "survey_map_douala.jpg"),
        "fotouni": (REPO_ROOT / "data" / "fotouni" / "pilot_survey" / "survey_map.html",
                    REPO_ROOT / "data" / "fotouni" / "pilot_survey" / "fotouni.csv",
                    FIGURES / "survey_map_fotouni.jpg"),
    }
    for site, (html_path, csv_path, out_path) in jobs.items():
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        pts = []
        for r in rows:
            lat = r.get("rtk_lat") or r.get("mobile_lat")
            lon = r.get("rtk_lon") or r.get("mobile_lon")
            if lat and lon:
                pts.append((float(lat), float(lon)))
        bounds = _bounds_from_points(pts)
        _inject_and_screenshot(html_path, out_path, bounds, tile_wait_ms=11000)


if __name__ == "__main__":
    if CHROMIUM is None:
        raise SystemExit("No chromium/chrome binary found on PATH")
    screenshot_road_quality_drives()
    screenshot_addressing_survey_maps()
    screenshot_proximity_walkscores()
