"""Shared helpers for building the Douala / Fotouni walk-accessibility maps.

Pulled out of the per-place run scripts so both places share the exact same
logic for: loading survey points, snapping to the pedestrian graph,
network-distance accessibility, hex grid + population aggregation and the
custom Folium/Leaflet map with radio-style score layers, independent POI
checkboxes and a stats panel.

UrbanAccessAnalyzer has no packaging metadata, so it's put on sys.path
rather than declared as a uv dependency (see pyproject.toml comment).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import branca.colormap as bcm
import folium
import geopandas as gpd
import h3
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from folium import MacroElement
from jinja2 import Template
from shapely.geometry import LineString, Point, Polygon

sys.path.insert(0, "/home/miguel/Documents/Proyectos/python_libraries/UrbanAccessAnalyzer")
import UrbanAccessAnalyzer.graph_processing as uaa_graph  # noqa: E402
import UrbanAccessAnalyzer.osm as uaa_osm  # noqa: E402

from poi_categories import CATEGORIES, CATEGORY_ORDER, categorize_row, poi_label  # noqa: E402

WALK_THRESHOLD_M = 800  # ~10 min walk at ~4.8 km/h, see reports/methodology.md
UTM32N = "EPSG:32632"  # Cameroon (both AOIs) falls in UTM zone 32N
HEX_RESOLUTION = 9  # h3 resolution ~0.1 km^2 hexagons, see methodology.md
DISTANCE_SCALE_MAX_M = 1000  # fixed colour-scale ceiling for every category distance layer
MIN_POP_PER_HEX = 5  # hyperparam: hexes with fewer people than this are shown as 0 (see apply_min_population_threshold)
POP_BALL_MIN_R = 2  # px
POP_BALL_MAX_R = 24  # px
POP_BALL_EXPONENT = 1.6  # >1 (super-linear) exaggerates small-vs-large population differences beyond area-proportional (0.5)
DEFAULT_LAYER_OPACITY = 0.6  # initial hex/population layer fill opacity; live-adjustable via the map's opacity slider


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------
def load_points(csv_path: Path) -> gpd.GeoDataFrame:
    """Load survey CSV, pick rtk coords (fallback to mobile), tag categories."""
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    def pick_coord(row):
        try:
            lat, lon = float(row["rtk_lat"]), float(row["rtk_lon"])
            prec = float(row["rtk_precision"]) if row["rtk_precision"] else 0
            if lat and lon and -90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= prec < 50:
                return lat, lon, "rtk"
        except (ValueError, KeyError):
            pass
        try:
            lat, lon = float(row["mobile_lat"]), float(row["mobile_lon"])
            if lat and lon and -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon, "mobile"
        except (ValueError, KeyError):
            pass
        return None, None, None

    coords = df.apply(pick_coord, axis=1, result_type="expand")
    coords.columns = ["lat", "lon", "coord_source"]
    df = pd.concat([df, coords], axis=1).dropna(subset=["lat", "lon"])

    df["categories"] = df.apply(
        lambda r: categorize_row(r["landuse"], r["products"], r["comments_landuse"]), axis=1
    )
    gdf = gpd.GeoDataFrame(
        df, geometry=[Point(lon, lat) for lat, lon in zip(df["lat"], df["lon"])], crs="EPSG:4326"
    )
    return gdf


def poi_gdf_for_category(points: gpd.GeoDataFrame, cat_key: str) -> gpd.GeoDataFrame:
    mask = points["categories"].apply(lambda s: cat_key in s)
    return points[mask]


# ---------------------------------------------------------------------------
# Streets (pedestrian graph)
# ---------------------------------------------------------------------------
def get_pedestrian_graph(aoi_4326: gpd.GeoSeries, cache_dir: Path, buffer_deg: float = 0.003):
    """Download (or reuse cached) OSM walk network covering the AOI, via
    UrbanAccessAnalyzer.osm.download_graph. Returns an osmnx graph in its
    native lat/lon CRS plus a projected (metres) copy used for distances."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    graphml_path = cache_dir / "pedestrian_graph.graphml"
    edges_path = cache_dir / "streets_edges.gpkg"
    nodes_path = cache_dir / "streets_nodes.gpkg"

    if graphml_path.exists():
        G = ox.load_graphml(graphml_path)
    else:
        aoi_buffered = aoi_4326.to_crs(UTM32N).buffer(buffer_deg * 111_000).to_crs("EPSG:4326")
        try:
            G = uaa_osm.download_graph(aoi_buffered, network_type="walk")
        except Exception:
            poly = aoi_buffered.iloc[0] if hasattr(aoi_buffered, "iloc") else aoi_buffered
            G = ox.graph_from_polygon(poly, network_type="walk", simplify=True)
        ox.save_graphml(G, graphml_path)
        nodes, edges = ox.graph_to_gdfs(G)
        edges.reset_index().to_file(edges_path, driver="GPKG")
        nodes.reset_index().to_file(nodes_path, driver="GPKG")

    G_proj = ox.project_graph(G, to_crs=UTM32N)
    return G, G_proj


def nearest_node(G_proj, gdf_4326: gpd.GeoDataFrame) -> list:
    if len(gdf_4326) == 0:
        return []
    proj = gdf_4326.to_crs(UTM32N)
    xs = proj.geometry.x.values
    ys = proj.geometry.y.values
    return list(ox.distance.nearest_nodes(G_proj, xs, ys))


def _insert_points_as_nodes(G_base, points_4326: gpd.GeoDataFrame, id_base: int,
                             min_dist: float = 1.0, tolerance: float = 0.5):
    """Insert every point into the graph as a real node — splitting its
    nearest edge exactly at the projected point — via
    UrbanAccessAnalyzer.graph_processing.add_points_to_graph, the library's
    own mechanism for attaching POIs/query locations to a street network
    (used internally by its isochrone/accessibility pipeline). We do not
    reimplement this: it is installed, not modified.

    A point that lands within `min_dist` of an existing node is *not* split
    in (a new node there would be degenerate/near-zero-length) — the
    library silently skips it, and we fall back to snapping it to that
    already-nearby existing node via `graph_processing.nearest_nodes`,
    which is correct since the point is, by construction, within
    `min_dist` metres of it.

    Returns (G_with_points, node_ids) where node_ids[i] is always a real
    graph node id representing points_4326.iloc[i] (never None/missing).
    """
    n = len(points_4326)
    if n == 0:
        return G_base, []
    points_4326 = points_4326.reset_index(drop=True)
    custom_ids = [-(id_base + i) for i in range(n)]
    G_new, inserted_ids = uaa_graph.add_points_to_graph(
        points_4326, G_base, min_dist=min_dist, indices=custom_ids, tolerance=tolerance
    )
    inserted_set = set(inserted_ids)
    node_ids = [cid if cid in inserted_set else None for cid in custom_ids]

    missing_pos = [i for i, nid in enumerate(node_ids) if nid is None]
    if missing_pos:
        missing_pts = points_4326.iloc[missing_pos].reset_index(drop=True)
        geom_idx, node_idx = uaa_graph.nearest_nodes(missing_pts, G_new)
        for gi, nid in zip(geom_idx, node_idx):
            node_ids[missing_pos[gi]] = nid

    return G_new, node_ids


# ---------------------------------------------------------------------------
# Hex grid
# ---------------------------------------------------------------------------
def pick_hex_resolution(aoi_4326_polygon) -> int:
    """Fixed resolution for both AOIs: h3 res 10 has an average edge length
    of ~66m, i.e. hexagons with a ~50m circumradius, matching the pedestrian
    proximity scale we want to resolve at for both places."""
    return 10  # ~0.015 km^2 hexagons, edge ~66m (~50m radius)


def build_hex_grid(aoi_4326_polygon, resolution: int = HEX_RESOLUTION) -> gpd.GeoDataFrame:
    cells = h3.geo_to_cells(aoi_4326_polygon.__geo_interface__, resolution) if hasattr(h3, "geo_to_cells") else None
    if cells is None:
        # h3 v4 API
        from h3 import LatLngPoly
        coords = [(lat, lon) for lon, lat in aoi_4326_polygon.exterior.coords]
        poly = LatLngPoly(coords)
        cells = h3.polygon_to_cells(poly, resolution)
    rows = []
    for c in cells:
        boundary = h3.cell_to_boundary(c)  # list of (lat, lon)
        poly = Polygon([(lon, lat) for lat, lon in boundary])
        centroid_lat, centroid_lon = h3.cell_to_latlng(c)
        rows.append({"hex_id": c, "geometry": poly, "centroid": Point(centroid_lon, centroid_lat)})
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    return gdf


# ---------------------------------------------------------------------------
# Accessibility (real network distance, metres) via UrbanAccessAnalyzer's
# own node-insertion + shortest-path machinery
# ---------------------------------------------------------------------------
_HEX_NODE_ID_BASE = 10_000_000
_POI_NODE_ID_BASE = 20_000_000


def compute_category_distances(hex_gdf, points, G, G_proj) -> pd.DataFrame:
    """For every hexagon and every category, the real pedestrian-network
    distance (m) from the hexagon centroid to the nearest POI of that
    category, plus a derived 0-1 accessibility score (UrbanAccessAnalyzer
    convention: higher=better) used only internally for the walk-score
    threshold logic.

    Every hex centroid and every POI is spliced into the graph as a real
    node via `_insert_points_as_nodes` (UrbanAccessAnalyzer's
    `graph_processing.add_points_to_graph`) — the nearest edge is split
    exactly at the point's projection, exactly as the library's own
    isochrone/accessibility pipeline does it, rather than approximating
    "last mile" access with a virtual node bolted on outside the graph.

    Hex centroids are inserted once, into the shared base graph. Each
    category then gets its own copy of that graph with only its own POIs
    spliced in (`add_points_to_graph` returns a new graph rather than
    mutating in place, so this is cheap and keeps every category's splits
    independent of one another, matching the fact that each category's
    accessible/isochrone shape is different).
    """
    # Insertion goes through UrbanAccessAnalyzer's add_points_to_graph, which
    # rebuilds the graph via osmnx's graph_from_gdfs — always a *directed*
    # MultiDiGraph, regardless of what was passed in. So we keep G_proj
    # directed through every insertion step, and only take `.to_undirected()`
    # right before running Dijkstra (pedestrian access is bidirectional);
    # taking it before insertion silently degrades back to directed anyway
    # and quietly breaks connectivity (most nodes only kept one direction).
    centroids_4326 = gpd.GeoDataFrame(geometry=hex_gdf["centroid"].values, crs="EPSG:4326")
    G_with_hex, hex_node_ids = _insert_points_as_nodes(G_proj, centroids_4326, id_base=_HEX_NODE_ID_BASE)

    out = pd.DataFrame(index=hex_gdf.index)
    out["hex_id"] = hex_gdf["hex_id"].values

    for cat_key in CATEGORY_ORDER:
        poi = poi_gdf_for_category(points, cat_key)
        col_dist = f"{cat_key}_distance_m"
        col_score = f"{cat_key}_score"
        if len(poi) == 0:
            out[col_dist] = np.nan
            out[col_score] = 0.0
            continue

        G_cat, poi_node_ids = _insert_points_as_nodes(G_with_hex, poi, id_base=_POI_NODE_ID_BASE)
        dist_from_pois = nx.multi_source_dijkstra_path_length(
            G_cat.to_undirected(), sources=set(poi_node_ids), weight="length"
        )
        dists = [dist_from_pois.get(n, np.nan) for n in hex_node_ids]

        out[col_dist] = dists
        # 0-1 decay score, internal use only (never shown as the display layer)
        out[col_score] = [1.0 / (1.0 + d / WALK_THRESHOLD_M) if not np.isnan(d) else 0.0 for d in dists]

    return out


def compute_walk_score(dist_df: pd.DataFrame, threshold_m: float = WALK_THRESHOLD_M) -> pd.Series:
    accessible = pd.DataFrame(
        {
            cat: (dist_df[f"{cat}_distance_m"] <= threshold_m).astype(int)
            for cat in CATEGORY_ORDER
        }
    )
    return accessible.sum(axis=1)


# ---------------------------------------------------------------------------
# Population (WorldPop, per hex)
# ---------------------------------------------------------------------------
def population_per_hex(hex_gdf: gpd.GeoDataFrame, worldpop_tif: Path) -> pd.Series:
    """Population per hex via areal-weighted interpolation.

    WorldPop pixels (100m) are vectorised into polygons, then each pixel's
    population is redistributed across every hex it overlaps, in proportion
    to the overlap area (piece_area / pixel_area). This matters whenever
    hexes are smaller than a raster pixel (e.g. Douala's fine h3-12 grid,
    ~9m edge, well under the 100m WorldPop cell): naively summing "whichever
    pixels touch this hex" — as a per-hex rasterio.mask crop does — assigns
    each pixel's *entire* population to *every* hex that touches it, wildly
    inflating the total. Area-weighting instead spreads that one pixel's
    population out across all the hexes sharing it, which is exact
    (population-conserving) regardless of whether hexes are smaller or
    larger than the raster cell.
    """
    import rasterio
    from rasterio.features import shapes as rio_shapes
    from rasterio.windows import from_bounds
    from shapely.geometry import shape as shapely_shape

    with rasterio.open(worldpop_tif) as src:
        bounds = hex_gdf.to_crs(src.crs).total_bounds
        pad = max(abs(src.res[0]), abs(src.res[1])) * 2
        window = from_bounds(bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad, transform=src.transform)
        window = window.round_offsets().round_lengths()
        arr = src.read(1, window=window)
        arr = np.where(arr < 0, 0, arr).astype("float32")
        win_transform = src.window_transform(window)
        valid = arr > 0
        pixel_crs = src.crs
        polys, vals = [], []
        if valid.any():
            for geom, val in rio_shapes(arr, mask=valid, transform=win_transform):
                polys.append(shapely_shape(geom))
                vals.append(float(val))

    if not polys:
        return pd.Series([0.0] * len(hex_gdf), index=hex_gdf.index)

    # area ratios must be computed in a projected (metric) CRS — WorldPop
    # rasters ship in EPSG:4326, where "area" in degrees^2 is meaningless.
    pix_gdf = gpd.GeoDataFrame({"pop": vals}, geometry=polys, crs=pixel_crs).to_crs(UTM32N)
    pix_gdf["pixel_area"] = pix_gdf.geometry.area

    hex_proj = gpd.GeoDataFrame(
        {"_hex_pos": range(len(hex_gdf))}, geometry=hex_gdf.geometry.values, crs=hex_gdf.crs
    ).to_crs(UTM32N)

    inter = gpd.overlay(hex_proj, pix_gdf, how="intersection")
    if len(inter) == 0:
        return pd.Series([0.0] * len(hex_gdf), index=hex_gdf.index)
    inter["pop_share"] = inter["pop"] * (inter.geometry.area / inter["pixel_area"])
    per_pos = inter.groupby("_hex_pos")["pop_share"].sum()
    pops = [float(per_pos.get(i, 0.0)) for i in range(len(hex_gdf))]
    return pd.Series(pops, index=hex_gdf.index)


def apply_min_population_threshold(pop_series: pd.Series, min_pop: float = MIN_POP_PER_HEX) -> pd.Series:
    """Zero out hexes with fewer than `min_pop` people (WorldPop's raw
    per-hex disaggregation has a long tail of near-zero, sub-single-person
    hexes that are noise rather than signal at this scale), then rescale
    the remaining hexes so the AOI total population is unchanged.

    At very fine hex resolutions (smaller than the raster cell, e.g.
    Douala's h3-12 grid against 100m WorldPop pixels — see
    `population_per_hex`), a single pixel's population is spread thin
    across many hexes, so most/all individual hexes can legitimately sit
    below an absolute-headcount threshold even though the AOI is populated.
    If thresholding would zero out every hex, it's a resolution mismatch,
    not a real "unpopulated" signal — skip it rather than silently
    reporting zero population everywhere."""
    total_before = float(pop_series.sum())
    adjusted = pop_series.where(pop_series >= min_pop, 0.0)
    total_after = float(adjusted.sum())
    if total_after <= 0:
        return pop_series
    return adjusted * (total_before / total_after)


def _pop_radius(p: float, pmax: float) -> float:
    if pmax <= 0:
        return POP_BALL_MIN_R
    frac = max(p, 0.0) / pmax
    return POP_BALL_MIN_R + (POP_BALL_MAX_R - POP_BALL_MIN_R) * frac ** POP_BALL_EXPONENT


# ---------------------------------------------------------------------------
# Map building
# ---------------------------------------------------------------------------
_CONTROL_JS = Template(
    """
{% macro script(this, kwargs) %}
(function() {
  var hexLayers = {{ this.hex_layers_js }};        // key -> layer var name
  var popLayers = {{ this.pop_layers_js }};         // key -> layer var name
  var streetLayers = {{ this.street_layers_js }};   // key -> layer var name
  var poiLayers = {{ this.poi_layers_js }};         // catKey -> layer var name
  var legends = {{ this.legend_js }};               // key -> div id
  var popSizeLegendId = {{ this.pop_size_legend_id }};
  var stats = {{ this.stats_json }};
  var basemapLayers = {{ this.basemap_layers_js }};  // key -> layer var name
  var map = {{ this.map_name }};

  function hideGroup(layers) {
    Object.keys(layers).forEach(function(k) {
      var l = window[layers[k]];
      if (l && map.hasLayer(l)) map.removeLayer(l);
    });
  }

  window.onBasemapChange = function(sel) {
    hideGroup(basemapLayers);
    var l = window[basemapLayers[sel.value]];
    if (l) l.addTo(map);
  };
  function hideAllPoi() {
    Object.keys(poiLayers).forEach(function(k) {
      var l = window[poiLayers[k]];
      if (l && map.hasLayer(l)) map.removeLayer(l);
      var cb = document.getElementById('poi-cb-' + k);
      if (cb) cb.checked = false;
    });
  }

  window.currentOpacityFrac = {{ this.default_opacity }};

  function applyOpacity(layerVarName) {
    var l = window[layerVarName];
    if (!l || !l.eachLayer) return;
    l.eachLayer(function(sub) {
      if (sub.feature && sub.feature.properties && sub.feature.properties.empty) return;
      if (sub.setStyle) sub.setStyle({fillOpacity: window.currentOpacityFrac});
    });
  }

  window.onOpacityChange = function(val) {
    window.currentOpacityFrac = val / 100;
    var key = document.getElementById('score-select').value;
    var base = document.getElementById('base-select').value;
    if (key === 'none') return;
    applyOpacity(base === 'hex' ? hexLayers[key] : popLayers[key]);
  };

  window.refreshLayers = function() {
    var key = document.getElementById('score-select').value;
    var base = document.getElementById('base-select').value;
    var streetsOn = document.getElementById('streets-cb').checked;

    hideGroup(hexLayers);
    hideGroup(popLayers);
    hideGroup(streetLayers);
    Object.keys(legends).forEach(function(k) {
      var el = document.getElementById(legends[k]);
      if (el) el.style.display = 'none';
    });
    var popSizeEl = document.getElementById(popSizeLegendId);
    if (popSizeEl) popSizeEl.style.display = 'none';

    if (key !== 'none') {
      if (base === 'hex') {
        var l = window[hexLayers[key]];
        if (l) { l.addTo(map); applyOpacity(hexLayers[key]); }
      } else {
        var l2 = window[popLayers[key]];
        if (l2) { l2.addTo(map); applyOpacity(popLayers[key]); }
        if (popSizeEl) popSizeEl.style.display = 'block';
      }
      var leg = document.getElementById(legends[key]);
      if (leg) leg.style.display = 'block';
      if (streetsOn) {
        var sl = window[streetLayers[key]];
        if (sl) sl.addTo(map);
      }
    }
    updateStats(key);
  };

  window.onScoreChange = function(sel) {
    var val = sel.value;
    if (val === 'walk_score') {
      // walk score depends on every category: activate all POI layers/checkboxes
      Object.keys(poiLayers).forEach(function(k) {
        var l = window[poiLayers[k]];
        if (l && !map.hasLayer(l)) l.addTo(map);
        var cb = document.getElementById('poi-cb-' + k);
        if (cb) cb.checked = true;
      });
    } else if (val !== 'none') {
      // radio behaviour: selecting a single category score auto-selects only its POI layer
      hideAllPoi();
      var l2 = window[poiLayers[val]];
      if (l2) l2.addTo(map);
      var cb = document.getElementById('poi-cb-' + val);
      if (cb) cb.checked = true;
    }
    window.refreshLayers();
  };

  window.onBaseChange = function() { window.refreshLayers(); };
  window.onStreetsToggle = function() { window.refreshLayers(); };

  window.onPoiToggle = function(catKey, checked) {
    var l = window[poiLayers[catKey]];
    if (!l) return;
    if (checked) l.addTo(map);
    else if (map.hasLayer(l)) map.removeLayer(l);
  };

  window.toggleStatsPanel = function() {
    var p = document.getElementById('stats-panel');
    p.style.display = (p.style.display === 'none' || !p.style.display) ? 'block' : 'none';
    var sel = document.getElementById('score-select');
    updateStats(sel ? sel.value : 'none');
  };

  window.updateStats = function(key) {
    var body = document.getElementById('stats-body');
    if (!body) return;
    if (key === 'none' || !stats[key]) {
      body.innerHTML = '<b>' + stats.total_population.toFixed(0) + '</b> people in AOI (WorldPop). Select a layer to see its breakdown.';
      return;
    }
    var s = stats[key];
    var html = '<b>' + s.title + '</b><br>';
    if (s.weighted_mean !== null && s.weighted_mean !== undefined) {
      html += '<span style="color:#555">Population-weighted average: <b>' + s.weighted_mean.toFixed(1) + ' ' + s.unit + '</b></span><br>';
    }
    html += '<table style="width:100%;border-collapse:collapse;margin-top:4px">';
    html += '<tr style="font-weight:bold;border-bottom:1px solid #ccc"><td>class</td><td style="text-align:right">pop.</td><td style="text-align:right">%</td></tr>';
    s.rows.forEach(function(r, i) {
      var bold = (r.label === 'Total') ? 'font-weight:bold;border-top:1px solid #ccc' : '';
      html += '<tr style="' + bold + '"><td>' + r.label + '</td><td style="text-align:right">' + r.pop.toFixed(0) + '</td><td style="text-align:right">' + r.pct.toFixed(1) + '%</td></tr>';
    });
    html += '</table>';
    body.innerHTML = html;
  };

  map.whenReady(function() {
    var bm = document.getElementById('basemap-select');
    if (bm) window.onBasemapChange(bm);
    // Route through onScoreChange (rather than refreshLayers directly) so
    // the default-selected "walk score" option also gets its side effects
    // (activating every category's POI layer/checkbox), same as picking
    // it manually from the dropdown would.
    var sel = document.getElementById('score-select');
    if (sel) window.onScoreChange(sel); else window.refreshLayers();
  });
})();
{% endmacro %}
"""
)


class ControlScript(MacroElement):
    """Injects the JS glue that wires the score <select>, base-layer <select>,
    streets checkbox, POI checkboxes and stats panel to the Leaflet layers
    created in Python."""

    def __init__(self, map_name, hex_layers_js, pop_layers_js, street_layers_js,
                 poi_layers_js, legend_js, pop_size_legend_id, stats_json, basemap_layers_js,
                 default_opacity=DEFAULT_LAYER_OPACITY):
        super().__init__()
        self._template = _CONTROL_JS
        self.map_name = map_name
        self.hex_layers_js = hex_layers_js
        self.pop_layers_js = pop_layers_js
        self.street_layers_js = street_layers_js
        self.poi_layers_js = poi_layers_js
        self.legend_js = legend_js
        self.pop_size_legend_id = pop_size_legend_id
        self.stats_json = stats_json
        self.basemap_layers_js = basemap_layers_js
        self.default_opacity = default_opacity


def _assign_edges_to_hex(edges_gdf: gpd.GeoDataFrame, hex_gdf: gpd.GeoDataFrame) -> pd.Series:
    """Map each street edge to the hexagon its midpoint falls in (nearest
    hexagon centroid as a fallback for edges just outside the grid)."""
    edges_proj = edges_gdf.to_crs(UTM32N)
    mids = edges_proj.geometry.centroid
    mid_gdf = gpd.GeoDataFrame(geometry=list(mids), crs=UTM32N, index=edges_gdf.index)
    hexes = hex_gdf[["hex_id", "geometry"]].to_crs(UTM32N)
    joined = gpd.sjoin(mid_gdf, hexes, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    missing = joined["hex_id"].isna()
    if missing.any():
        nearest = gpd.sjoin_nearest(mid_gdf.loc[missing], hexes)
        nearest = nearest[~nearest.index.duplicated(keep="first")]
        joined.loc[missing, "hex_id"] = nearest["hex_id"]
    return joined["hex_id"].reindex(edges_gdf.index)


def _build_street_layer(m, edges_gdf, edge_hex_id, hex_pos, values, cmap, key) -> str:
    fg = folium.FeatureGroup(name=f"streets_{key}", show=False)
    for idx, geom in edges_gdf.geometry.items():
        hid = edge_hex_id.get(idx)
        pos = hex_pos.get(hid)
        v = values[pos] if pos is not None else float("nan")
        color = cmap(v) if (pos is not None and v == v) else "#999999"
        folium.GeoJson(
            geom.__geo_interface__,
            style_function=(lambda color: (lambda x: {"color": color, "weight": 3, "opacity": 0.85}))(color),
        ).add_to(fg)
    fg.add_to(m)
    return fg.get_name()


def _build_pop_layer(m, hex_gdf, pop_series, pmax, values, cmap, key, tooltip_fn) -> str:
    """Population bubbles: borderless, size = population, colour = the given
    score/distance layer. Hexes with zero population get no bubble at all."""
    fg = folium.FeatureGroup(name=f"pop_{key}", show=False)
    for i, geom in enumerate(hex_gdf.geometry):
        p = pop_series.iloc[i]
        if p <= 0:
            continue
        v = values[i]
        color = cmap(v) if v == v else "#999999"
        centroid = geom.centroid
        radius = _pop_radius(p, pmax)
        folium.CircleMarker(
            [centroid.y, centroid.x],
            radius=radius,
            fill=True,
            fill_color=color,
            fill_opacity=DEFAULT_LAYER_OPACITY,
            weight=0,
            stroke=False,
            tooltip=tooltip_fn(v, p),
        ).add_to(fg)
    fg.add_to(m)
    return fg.get_name()


def build_map(
    place: str,
    center_latlon,
    hex_gdf: gpd.GeoDataFrame,
    dist_df: pd.DataFrame,
    walk_score: pd.Series,
    pop_series: pd.Series,
    points: gpd.GeoDataFrame,
    out_html: Path,
    G=None,
    default_zoom: int = 16,
    default_base: str = "hex",
):
    # max_zoom=28 lets the user zoom in far past any basemap's native tile
    # resolution — Leaflet just upscales the last available tile (maxNativeZoom
    # stays at each layer's real limit) instead of capping the zoom control.
    m = folium.Map(location=center_latlon, zoom_start=default_zoom, tiles=None, control_scale=True, max_zoom=28)
    map_name = m.get_name()

    # -- Basemap switcher: CartoDB Positron (default) vs. Google Hybrid @60% opacity --
    basemap_cartodb = folium.TileLayer(tiles="cartodbpositron", name="CartoDB Positron",
                                        overlay=False, control=False, show=True,
                                        max_zoom=28, max_native_zoom=20)
    basemap_cartodb.add_to(m)
    basemap_google = folium.TileLayer(
        tiles="https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        attr="Google", name="Google Hybrid", overlay=False, control=False,
        opacity=0.6, show=False, max_zoom=28, max_native_zoom=20,
    )
    basemap_google.add_to(m)
    basemap_layers_js = {"cartodb": basemap_cartodb.get_name(), "google_hybrid": basemap_google.get_name()}

    hex_layers_js = {}
    pop_layers_js = {}
    street_layers_js = {}
    legend_js = {}
    legend_div_html = []
    pmax = pop_series.max() if pop_series.max() > 0 else 1
    hex_pos = {hid: i for i, hid in enumerate(hex_gdf["hex_id"])}

    edges_gdf, edge_hex_id = None, None
    if G is not None:
        try:
            edges_gdf = ox.graph_to_gdfs(G, nodes=False, edges=True)
            if edges_gdf.crs is None:
                edges_gdf = edges_gdf.set_crs("EPSG:4326")
            edges_gdf = edges_gdf.to_crs("EPSG:4326")
            edge_hex_id = _assign_edges_to_hex(edges_gdf, hex_gdf)
        except Exception:
            edges_gdf, edge_hex_id = None, None

    def add_hex_layer(key, values, cmap, tooltip_label_fn):
        fg = folium.FeatureGroup(name=f"hex_{key}", show=False)
        for i, geom in enumerate(hex_gdf.geometry):
            v = values[i]
            p = pop_series.iloc[i]
            empty = p <= 0
            if empty:
                # empty cell: fully transparent, no fill at all, and exempt
                # from the opacity slider (it must always stay invisible)
                style = {"fillOpacity": 0, "opacity": 0, "weight": 0, "fillColor": "#ffffff"}
            else:
                color = cmap(v) if v == v else "#cccccc"
                style = {"fillColor": color, "color": "transparent", "weight": 0, "fillOpacity": DEFAULT_LAYER_OPACITY}
            feature = {"type": "Feature", "properties": {"empty": bool(empty)}, "geometry": geom.__geo_interface__}
            folium.GeoJson(
                feature,
                style_function=(lambda style: (lambda x: style))(style),
                tooltip=tooltip_label_fn(v),
            ).add_to(fg)
        fg.add_to(m)
        return fg.get_name()

    def add_street_layer(key, values, cmap):
        if edges_gdf is None:
            return None
        return _build_street_layer(m, edges_gdf, edge_hex_id, hex_pos, values, cmap, key)

    # -- Walk score: hex layer, population layer, street layer --
    ws_values = walk_score.tolist()
    ws_cmap = bcm.LinearColormap(["#a50026", "#fee08b", "#1a9850"], vmin=0, vmax=11, caption="Walk score (0-11 categories)")
    hex_layers_js["walk_score"] = add_hex_layer("walk_score", ws_values, ws_cmap, lambda v: f"Walk score: {v}/11")
    legend_js["walk_score"] = "legend-walk_score"
    legend_div_html.append(_legend_div("legend-walk_score", ws_cmap, "Walk score", "0 (worst) — 11 (best) accessible categories", display="none"))
    pop_layers_js["walk_score"] = _build_pop_layer(
        m, hex_gdf, pop_series, pmax, values=ws_values, cmap=ws_cmap, key="walk_score",
        tooltip_fn=lambda v, p: f"Walk score: {v}/11 — population: {p:.0f}",
    )
    sl = add_street_layer("walk_score", ws_values, ws_cmap)
    if sl:
        street_layers_js["walk_score"] = sl

    stats = {"total_population": float(pop_series.sum())}
    ws_arr = np.array(ws_values, dtype=float)
    stats["walk_score"] = {"title": "Walk score — population by class", **_bucket_stats(ws_arr, pop_series, is_walk_score=True)}

    # -- Per-category: distance hex layer, population layer, street layer, POI markers --
    poi_layers_js = {}
    for cat_key in CATEGORY_ORDER:
        name, emoji, _tags, _re = CATEGORIES[cat_key]
        dist_col = f"{cat_key}_distance_m"
        dvals = dist_df[dist_col].values
        cmap = bcm.LinearColormap(["#00441b", "#66c2a4", "#d9f0a3", "#fee08b", "#a50026"],
                                   vmin=0, vmax=DISTANCE_SCALE_MAX_M,
                                   caption=f"{name} — network distance (m)")
        hex_layers_js[cat_key] = add_hex_layer(
            cat_key, dvals, cmap, lambda v: f"{name}: {'n/a' if v != v else f'{v:.0f} m'}"
        )
        legend_js[cat_key] = f"legend-{cat_key}"
        legend_div_html.append(_legend_div(f"legend-{cat_key}", cmap, f"{emoji} {name} distance",
                                            f"metres to nearest POI, capped at {DISTANCE_SCALE_MAX_M} m (lower = better)",
                                            display="none"))
        pop_layers_js[cat_key] = _build_pop_layer(
            m, hex_gdf, pop_series, pmax, values=dvals, cmap=cmap, key=cat_key,
            tooltip_fn=lambda v, p: f"{name}: {'n/a' if v != v else f'{v:.0f} m'} — population: {p:.0f}",
        )
        sl = add_street_layer(cat_key, dvals, cmap)
        if sl:
            street_layers_js[cat_key] = sl

        stats[cat_key] = {"title": f"{emoji} {name} — population by distance band",
                           **_bucket_stats(dvals, pop_series, is_walk_score=False)}

        # POI markers
        fg_poi = folium.FeatureGroup(name=f"poi_{cat_key}", show=False)
        sub = poi_gdf_for_category(points, cat_key)
        for _, row in sub.iterrows():
            folium.Marker(
                [row.geometry.y, row.geometry.x],
                icon=folium.DivIcon(html=(
                    '<div style="font-size:18px; -webkit-text-stroke:1px black; '
                    'text-shadow:-1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, '
                    f'1px 1px 0 #000;">{emoji}</div>'
                )),
                tooltip=f"{emoji} {name}: {poi_label(row.get('products'), row.get('comments_landuse'))}",
            ).add_to(fg_poi)
        fg_poi.add_to(m)
        poi_layers_js[cat_key] = fg_poi.get_name()

    # -- Population-size legend (ball size -> population; shown alongside the colour legend) --
    pop_size_div_id = "legend-pop-size"
    legend_div_html.append(_pop_size_legend_div(pop_size_div_id, pmax, display="none"))

    # -- Controls panel (HTML) --
    controls_html = _build_controls_html(place, default_base=default_base)
    m.get_root().html.add_child(folium.Element(controls_html))

    # Legends are stacked in a single flex column anchored bottom-right, so
    # whichever legend(s) are visible at once (colour + population-size) are
    # laid out one above the other automatically instead of at fixed pixel
    # offsets that overlap when content wraps to an extra line.
    stack_open = ('<div id="legend-stack" style="position:fixed; bottom:60px; right:10px; z-index:900; '
                  'display:flex; flex-direction:column-reverse; align-items:flex-end; gap:8px;">')
    m.get_root().html.add_child(folium.Element(stack_open))
    for div in legend_div_html:
        m.get_root().html.add_child(folium.Element(div))
    m.get_root().html.add_child(folium.Element("</div>"))

    script = ControlScript(
        map_name=map_name,
        hex_layers_js=json.dumps(hex_layers_js),
        pop_layers_js=json.dumps(pop_layers_js),
        street_layers_js=json.dumps(street_layers_js),
        poi_layers_js=json.dumps(poi_layers_js),
        legend_js=json.dumps(legend_js),
        pop_size_legend_id=json.dumps(pop_size_div_id),
        stats_json=json.dumps(stats),
        basemap_layers_js=json.dumps(basemap_layers_js),
    )
    m.get_root().add_child(script)

    out_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_html))
    return stats


def _weighted_mean(arr: np.ndarray, pop_series: pd.Series):
    """Population-weighted average value, ignoring hexes with no valid
    value (unreachable categories) and hexes with zero population."""
    w = pop_series.to_numpy(dtype=float)
    mask = ~np.isnan(arr) & (w > 0)
    if not mask.any() or w[mask].sum() <= 0:
        return None
    return float(np.sum(arr[mask] * w[mask]) / np.sum(w[mask]))


def _bucket_stats(values, pop_series, is_walk_score: bool):
    """Class breakdown + a Total row: population and % of AOI population in
    each class of the given layer (walk-score count, or distance in metres),
    plus the population-weighted average of the layer itself."""
    arr = np.asarray(values, dtype=float)
    total_pop = float(pop_series.sum()) or 1.0
    weighted_mean = _weighted_mean(arr, pop_series)

    if is_walk_score:
        bins = [
            (0, 2, "0–2 (very low)"),
            (3, 4, "3–4 (low)"),
            (5, 6, "5–6 (moderate)"),
            (7, 8, "7–8 (high)"),
            (9, 11, "9–11 (very high)"),
        ]
        rows = []
        for lo, hi, label in bins:
            mask = (arr >= lo) & (arr <= hi)
            p = float(pop_series[mask].sum())
            rows.append({"label": label, "pop": p, "pct": 100 * p / total_pop})
        rows.append({"label": "Total", "pop": total_pop, "pct": 100.0})
        return {"unit": "categories", "rows": rows, "weighted_mean": weighted_mean}

    # 5 bands spanning the fixed 0-1000m colour scale (200m steps) plus a
    # 6th ">1000m" band that also absorbs unreachable hexes (NaN: no POI of
    # this category anywhere in the AOI) — 6 classes + Total.
    vmax = DISTANCE_SCALE_MAX_M
    edges = np.linspace(0, vmax, 6)  # 0,200,400,600,800,1000
    filled = np.where(np.isnan(arr), np.inf, arr)
    rows = []
    for i in range(5):
        lo, hi = edges[i], edges[i + 1]
        mask = (filled >= lo) & (filled < hi)
        p = float(pop_series[mask].sum())
        rows.append({"label": f"{lo:.0f}–{hi:.0f} m", "pop": p, "pct": 100 * p / total_pop})
    mask_far = filled >= vmax
    p_far = float(pop_series[mask_far].sum())
    rows.append({"label": f">{vmax:.0f} m / unreachable", "pop": p_far, "pct": 100 * p_far / total_pop})
    rows.append({"label": "Total", "pop": total_pop, "pct": 100.0})
    return {"unit": "m", "rows": rows, "weighted_mean": weighted_mean}


def _legend_div(div_id, cmap, title, subtitle, display="none"):
    stops = [cmap.rgb_hex_str(cmap.vmin + t * (cmap.vmax - cmap.vmin)) for t in np.linspace(0, 1, 6)]
    gradient_css = ", ".join(stops)
    return f"""
<div id="{div_id}" style="display:{display};
  background:white; padding:8px 10px; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.4); font-size:12px; width:190px;">
  <b>{title}</b><br><span style="color:#555">{subtitle}</span><br>
  <div style="height:10px;width:100%;margin-top:4px;background:linear-gradient(to right, {gradient_css});"></div>
  <div style="display:flex;justify-content:space-between"><span>{cmap.vmin:.0f}</span><span>{cmap.vmax:.0f}</span></div>
</div>
"""


def _pop_size_legend_div(div_id, pmax, display="none"):
    samples = [0.25, 0.6, 1.0]
    rows_html = []
    for frac in samples:
        p = pmax * frac
        radius = _pop_radius(p, pmax)
        d = radius * 2
        rows_html.append(
            f'<div style="display:flex;align-items:center;gap:6px;margin-top:3px">'
            f'<div style="width:{d:.0f}px;height:{d:.0f}px;border-radius:50%;background:#888;flex:0 0 auto"></div>'
            f'<span>{p:.0f} people</span></div>'
        )
    return f"""
<div id="{div_id}" style="display:{display};
  background:white; padding:8px 10px; border-radius:6px; box-shadow:0 1px 4px rgba(0,0,0,.4); font-size:12px; width:150px;">
  <b>Population (ball size)</b>
  {''.join(rows_html)}
</div>
"""


def _build_controls_html(place: str, default_base: str = "hex") -> str:
    options = ['<option value="none">None</option>', '<option value="walk_score" selected>Walk score</option>']
    for cat_key in CATEGORY_ORDER:
        name, emoji, _, _ = CATEGORIES[cat_key]
        options.append(f'<option value="{cat_key}">{emoji} {name} (distance)</option>')
    options_html = "\n".join(options)

    poi_checks = []
    for cat_key in CATEGORY_ORDER:
        name, emoji, _, _ = CATEGORIES[cat_key]
        poi_checks.append(
            f'<label style="display:block"><input type="checkbox" id="poi-cb-{cat_key}" '
            f'onchange="onPoiToggle(\'{cat_key}\', this.checked)"> {emoji} {name}</label>'
        )
    poi_checks_html = "\n".join(poi_checks)

    return f"""
<div style="position:fixed; top:10px; right:10px; z-index:9999; background:white; padding:10px 12px;
  border-radius:8px; box-shadow:0 1px 6px rgba(0,0,0,.4); font-size:13px; width:230px; max-height:85vh; overflow:auto;">
  <b>{place} — walk accessibility</b>
  <div style="margin-top:6px"><label>Score layer<br>
    <select id="score-select" onchange="onScoreChange(this)" style="width:100%">
      {options_html}
    </select></label></div>
  <div style="margin-top:6px"><label>Base layer<br>
    <select id="base-select" onchange="onBaseChange(this)" style="width:100%">
      <option value="hex"{' selected' if default_base == 'hex' else ''}>Hexagon grid</option>
      <option value="pop"{' selected' if default_base == 'pop' else ''}>Population</option>
    </select></label></div>
  <div style="margin-top:6px"><label>Basemap<br>
    <select id="basemap-select" onchange="onBasemapChange(this)" style="width:100%">
      <option value="cartodb">CartoDB (light)</option>
      <option value="google_hybrid">Google Hybrid (60% opacity)</option>
    </select></label></div>
  <div style="margin-top:6px"><label><input type="checkbox" id="streets-cb" onchange="onStreetsToggle(this.checked)">
    Show streets (coloured by active score)</label></div>
  <div style="margin-top:6px"><label>Hex/population layer opacity<br>
    <input type="range" id="opacity-slider" min="0" max="100" value="{int(DEFAULT_LAYER_OPACITY * 100)}"
      oninput="onOpacityChange(this.value)" style="width:100%"></label></div>
  <div style="margin-top:8px"><b>POI categories</b><div style="max-height:220px;overflow:auto">{poi_checks_html}</div></div>
</div>
<div id="stats-btn" onclick="toggleStatsPanel()" title="Show stats"
  style="position:fixed; bottom:55px; left:10px; z-index:900; background:white; width:38px; height:38px;
  border-radius:50%; box-shadow:0 1px 6px rgba(0,0,0,.4); display:flex; align-items:center; justify-content:center;
  cursor:pointer; font-size:20px;">📊</div>
<div id="stats-panel" style="display:none; position:fixed; bottom:100px; left:10px; z-index:900; background:white;
  padding:10px 12px; border-radius:8px; box-shadow:0 1px 6px rgba(0,0,0,.4); font-size:12px; width:280px; max-height:60vh; overflow:auto;">
  <div id="stats-body">Loading…</div>
</div>
"""
