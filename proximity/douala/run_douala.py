"""Build the Douala (urban) walk-accessibility map end to end.

Run with: uv run proximity/douala/run_douala.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

HERE = Path(__file__).resolve().parent
PROX = HERE.parent
sys.path.insert(0, str(PROX))

import map_utils as mu  # noqa: E402

PLACE = "Douala"
AOI_PATH = PROX / "aoi_douala.gpkg"
CSV_PATH = HERE / "douala.csv"
STREETS_DIR = PROX / "streets" / "douala"
POP_DIR = PROX / "population" / "douala"
WORLDPOP_TIF = PROX / "population" / "_raw" / "cmr_pop_2020_100m.tif"
OUT_HTML = HERE / "map.html"
OUT_STATS_CSV = HERE / "population_stats.csv"


def main():
    print(f"[{PLACE}] loading AOI + survey points")
    aoi = gpd.read_file(AOI_PATH).to_crs("EPSG:4326")
    aoi_poly = aoi.geometry.iloc[0]
    center = [aoi_poly.centroid.y, aoi_poly.centroid.x]

    points = mu.load_points(CSV_PATH)
    print(f"[{PLACE}] {len(points)} geolocated rows, "
          f"{points['categories'].apply(bool).sum()} tagged with >=1 POI category")

    print(f"[{PLACE}] fetching pedestrian graph")
    G, G_proj = mu.get_pedestrian_graph(aoi.geometry, STREETS_DIR)
    print(f"[{PLACE}] graph: {len(G.nodes)} nodes, {len(G.edges)} edges")

    # Douala is a small, dense pilot extent: one h3 resolution finer than the
    # default (res 11, ~25m edge) resolves individual streets/blocks better
    # than the shared res-10 default used for Fotouni.
    res = mu.pick_hex_resolution(aoi_poly) + 1
    print(f"[{PLACE}] hex grid resolution {res}")
    hex_gdf = mu.build_hex_grid(aoi_poly, resolution=res)
    print(f"[{PLACE}] {len(hex_gdf)} hexagons")

    print(f"[{PLACE}] computing per-category network distances")
    dist_df = mu.compute_category_distances(hex_gdf, points, G, G_proj)
    walk_score = mu.compute_walk_score(dist_df)

    print(f"[{PLACE}] aggregating WorldPop population per hex")
    if WORLDPOP_TIF.exists():
        pop_series = mu.population_per_hex(hex_gdf, WORLDPOP_TIF)
    else:
        print(f"[{PLACE}] WARNING: WorldPop raster missing at {WORLDPOP_TIF}, using zeros")
        pop_series = pd.Series([0.0] * len(hex_gdf))
    pop_series = mu.apply_min_population_threshold(pop_series)
    POP_DIR.mkdir(parents=True, exist_ok=True)
    hex_gdf.assign(population=pop_series.values).drop(columns=["centroid"]).to_file(
        POP_DIR / "hex_population.gpkg", driver="GPKG"
    )

    print(f"[{PLACE}] building map -> {OUT_HTML}")
    stats = mu.build_map(PLACE, center, hex_gdf, dist_df, walk_score, pop_series, points, OUT_HTML, G=G)

    # population_stats.csv: per-hex breakdown for every layer's accessible/not split
    out = hex_gdf[["hex_id"]].copy()
    out["population"] = pop_series.values
    out["walk_score"] = walk_score.values
    for cat in mu.CATEGORY_ORDER:
        out[f"{cat}_distance_m"] = dist_df[f"{cat}_distance_m"].values
        out[f"{cat}_accessible_{mu.WALK_THRESHOLD_M}m"] = (dist_df[f"{cat}_distance_m"] <= mu.WALK_THRESHOLD_M).astype(int).values
    out["pct_of_aoi_population"] = 100 * out["population"] / max(out["population"].sum(), 1e-9)
    out.to_csv(OUT_STATS_CSV, index=False)
    print(f"[{PLACE}] wrote {OUT_STATS_CSV}")
    print(f"[{PLACE}] done. total pop={pop_series.sum():.0f}, walk score mean={walk_score.mean():.2f}")


if __name__ == "__main__":
    main()
