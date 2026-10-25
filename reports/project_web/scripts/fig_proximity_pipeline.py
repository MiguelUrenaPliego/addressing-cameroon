"""Generate the proximity-analysis pipeline figure from the real,
already-computed Fotouni pilot data: the cached OSM pedestrian graph, the
H3 hexagon grid with WorldPop population, the categorized survey points
(via the project's own map_utils/poi_categories modules), and the final
per-category distance / walk-score results in population_stats.csv.

Run with the proximity project's own virtualenv (geopandas, osmnx,
networkx, rasterio, and now matplotlib are already installed there):

    proximity/.venv/bin/python \
        ../reports/FUASProjektarbeit/scripts/fig_proximity_pipeline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
import pandas as pd
import numpy as np
from matplotlib.lines import Line2D

REPO_ROOT = Path(__file__).resolve().parents[3]
PROX = REPO_ROOT / "code" / "proximity"
sys.path.insert(0, str(PROX))

import map_utils as mu  # noqa: E402
from poi_categories import CATEGORIES  # noqa: E402

OUT_PATH = REPO_ROOT / "reports" / "FUASProjektarbeit" / "figures" / "fig_proximity_pipeline.jpg"

PLACE = "fotouni"
AOI_PATH = PROX / f"aoi_{PLACE}.gpkg"
CSV_PATH = PROX / PLACE / f"{PLACE}.csv"
EDGES_PATH = PROX / "streets" / PLACE / "streets_edges.gpkg"
HEX_POP_PATH = PROX / "population" / PLACE / "hex_population.gpkg"
STATS_CSV = PROX / PLACE / "population_stats.csv"

FOCUS_CATEGORY = "school"


def main() -> None:
    aoi = gpd.read_file(AOI_PATH).to_crs("EPSG:4326")
    points = mu.load_points(CSV_PATH)
    edges = gpd.read_file(EDGES_PATH).to_crs("EPSG:4326")
    hex_pop = gpd.read_file(HEX_POP_PATH).to_crs("EPSG:4326")
    stats = pd.read_csv(STATS_CSV)
    hexes = hex_pop.merge(stats, on="hex_id", suffixes=("", "_stats"))

    # Keep only survey points that fall inside the pilot area's own AOI
    # polygon: the raw CSV extends slightly beyond it, and plotting those
    # extra points would misleadingly suggest they were part of this pilot.
    points = gpd.sjoin(points, aoi[["geometry"]], predicate="within").drop(columns=["index_right"])
    minx, miny, maxx, maxy = aoi.total_bounds
    pad_x, pad_y = 0.05 * (maxx - minx), 0.05 * (maxy - miny)
    xlim = (minx - pad_x, maxx + pad_x)
    ylim = (miny - pad_y, maxy + pad_y)
    print(f"{len(points)} survey rows (AOI-clipped), {len(edges)} street edges, {len(hexes)} final hexagons")

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 10.5))
    ((ax_a, ax_b), (ax_c, ax_d)) = axes

    # --- Panel (a): inputs -- pedestrian network, hex grid, POIs ----------
    edges.plot(ax=ax_a, color="#999999", linewidth=0.6, zorder=1)
    hexes.boundary.plot(ax=ax_a, color="#333333", linewidth=0.4, zorder=2)
    cat_colors = plt.get_cmap("tab10")
    handles = []
    for i, (key, (label, emoji, _tags, _re)) in enumerate(CATEGORIES.items()):
        sub = mu.poi_gdf_for_category(points, key)
        if len(sub) == 0:
            continue
        color = cat_colors(i % 10)
        sub.plot(ax=ax_a, color=color, markersize=14, zorder=3)
        handles.append(Line2D([0], [0], marker="o", color="none", markerfacecolor=color,
                               markersize=5, label=label))
    ax_a.legend(handles=handles, loc="upper left", fontsize=6, ncol=1, framealpha=0.9,
                handletextpad=0.3, labelspacing=0.3, borderpad=0.4)
    ax_a.set_title(f"(a) Network, hex grid, and POIs ({len(edges)} edges, {len(hexes)} hexagons)", fontsize=9.5)
    ax_a.set_xlim(*xlim)
    ax_a.set_ylim(*ylim)
    ax_a.set_xticks([])
    ax_a.set_yticks([])

    # --- Panel (b): one category's network-distance choropleth -----------
    dist_col = f"{FOCUS_CATEGORY}_distance_m"
    vals = hexes[dist_col]
    hexes.plot(ax=ax_b, column=dist_col, cmap="magma_r", linewidth=0.3, edgecolor="#555555",
               legend=True, missing_kwds={"color": "#dddddd", "label": "no route / n/a"},
               legend_kwds={"label": "network distance to nearest school (m)", "shrink": 0.75})
    sub = mu.poi_gdf_for_category(points, FOCUS_CATEGORY)
    sub.plot(ax=ax_b, color="cyan", edgecolor="black", markersize=40, marker="*", zorder=5)
    ax_b.set_title(f"(b) Network distance to nearest {FOCUS_CATEGORY} ({len(sub)} sites)", fontsize=9.5)
    ax_b.set_xlim(*xlim)
    ax_b.set_ylim(*ylim)
    ax_b.set_xticks([])
    ax_b.set_yticks([])

    # --- Panel (c): composite walk score choropleth -----------------------
    hexes.plot(ax=ax_c, column="walk_score", cmap="RdYlGn", vmin=0, vmax=11, linewidth=0.3,
               edgecolor="#555555", legend=True,
               legend_kwds={"label": "walk score (0-11)", "shrink": 0.75})
    ax_c.set_title("(c) Composite walk score", fontsize=9.5)
    ax_c.set_xlim(*xlim)
    ax_c.set_ylim(*ylim)
    ax_c.set_xticks([])
    ax_c.set_yticks([])

    # --- Panel (d): population share within 800 m, per category ----------
    cat_keys = list(CATEGORIES.keys())
    pct = []
    for key in cat_keys:
        acc_col = f"{key}_accessible_800m"
        acc_pop = hexes.loc[hexes[acc_col] == 1, "population"].sum()
        pct.append(100.0 * acc_pop / hexes["population"].sum())
    order = np.argsort(pct)[::-1]
    labels = [CATEGORIES[cat_keys[i]][0] for i in order]
    values = [pct[i] for i in order]
    bars = ax_d.barh(labels, values, color=plt.get_cmap("viridis")(np.linspace(0.15, 0.9, len(values))))
    ax_d.set_xlabel("% of AOI population within 800 m", fontsize=8.5)
    ax_d.set_title(f"(d) Population-weighted accessibility (pop. {int(hexes['population'].sum()):,})", fontsize=9.5)
    ax_d.tick_params(axis="y", labelsize=7.5)
    ax_d.set_xlim(0, 108)
    ax_d.invert_yaxis()
    for bar, v in zip(bars, values):
        ax_d.text(v + 1.5, bar.get_y() + bar.get_height() / 2, f"{v:.0f}%", va="center", fontsize=7.5)

    fig.suptitle(f"Walk-accessibility pipeline for the {PLACE.capitalize()} pilot area", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=170, pil_kwargs={"quality": 92})
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
