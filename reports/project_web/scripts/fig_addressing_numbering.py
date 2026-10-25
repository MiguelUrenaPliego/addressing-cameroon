"""Generate the address-numbering figure by running the real
osmaddresssystem pipeline (street_ids.label_streets ->
street_direction.assign_street_directionality -> numbering.assign_house_numbers)
on two small synthetic networks: a radial "organic" town and a planned grid.

Run with the OSMAddressSystem project's own virtualenv:

    OSMAddressSystem/.venv/bin/python \
        reports/FUASProjektarbeit/scripts/fig_addressing_numbering.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from matplotlib import cm
from shapely.geometry import LineString, Point

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "code" / "OSMAddressSystem"))

from osmaddresssystem import street_ids, street_direction as sd, numbering  # noqa: E402
from osmaddresssystem.synthetic_grid import build_idealized_grid  # noqa: E402

OUT_PATH = REPO_ROOT / "reports" / "FUASProjektarbeit" / "figures" / "fig_addressing_numbering.jpg"


def arm(angle_deg: float, length: float) -> tuple[float, float]:
    rad = math.radians(angle_deg)
    return (length * math.cos(rad), length * math.sin(rad))


def build_radial_graph() -> nx.MultiDiGraph:
    """A center O with 4 arms radiating outward at different lengths, plus
    one transversal street connecting two arm-tips at roughly the same
    radius (both endpoints ~equidistant from O -> classified transversal).
    """
    G = nx.MultiDiGraph(crs="EPSG:3857")
    O = (0.0, 0.0)
    # Angles chosen so every pair is > 20 deg apart mod 180 (the default
    # angle_threshold_deg), so the 4 arms are never accidentally merged
    # by the same direction-binning rule that (correctly) merges the
    # N-E transversal chord's own two halves at each of its endpoints.
    # N and E are given the SAME radius from O (200 m) so the N-E chord's
    # two endpoints are equidistant from the origin, which is exactly the
    # "roughly perpendicular to its own local radius" transversal test in
    # assign_street_directionality's radial mode.
    tips = {
        "N": arm(85, 200), "E": arm(350, 200), "S": arm(200, 120), "W": arm(110, 190),
    }
    G.add_node(0, x=O[0], y=O[1])
    ids = {"O": 0}
    for i, (label, (x, y)) in enumerate(tips.items(), start=1):
        G.add_node(i, x=x, y=y)
        ids[label] = i

    def add(u_label, v_label, highway="residential", name=None):
        u, v = ids[u_label], ids[v_label]
        pu = (G.nodes[u]["x"], G.nodes[u]["y"])
        pv = (G.nodes[v]["x"], G.nodes[v]["y"])
        fwd, bwd = LineString([pu, pv]), LineString([pv, pu])
        osmid = u * 100 + v
        G.add_edge(u, v, 0, highway=highway, name=name, geometry=fwd, length=fwd.length, osmid=osmid)
        G.add_edge(v, u, 0, highway=highway, name=name, geometry=bwd, length=bwd.length, osmid=osmid)

    add("O", "N")
    add("O", "E")
    add("O", "S")
    add("O", "W")
    # transversal arc approximated as a straight chord between N and E tips
    add("N", "E", highway="residential")
    return G, ids, tips


def run_radial():
    G, ids, tips = build_radial_graph()
    nodes_gdf, edges_gdf, streets_df = street_ids.label_streets(G, node_cluster_m=0)
    edges_gdf, streets_df = sd.assign_street_directionality(
        nodes_gdf, edges_gdf, streets_df, origin=(0.0, 0.0), numbering_mode="radial",
    )
    edges_gdf, streets_df = numbering.assign_house_numbers(edges_gdf, streets_df, number_divisor_m=20.0)
    print("Radial streets_df:\n", streets_df[["street_id", "road_type", "length_m"]])
    return edges_gdf, streets_df, ids, tips


def run_grid():
    G = build_idealized_grid(rows=4, cols=4, spacing=110.0, named_row=None, primary_row=None)
    nodes_gdf, edges_gdf, streets_df = street_ids.label_streets(G, node_cluster_m=0)
    edges_gdf, streets_df = sd.assign_street_directionality(
        nodes_gdf, edges_gdf, streets_df, origin=(0.0, 0.0), numbering_mode="grid", direction_deg=0.0,
    )
    edges_gdf, streets_df = numbering.assign_house_numbers(edges_gdf, streets_df, number_divisor_m=20.0)
    print("Grid streets_df:\n", streets_df[["street_id", "road_type", "length_m"]])
    return edges_gdf, streets_df


def plot_edges_by_distance(ax, edges_gdf, cmap_name="viridis", n_seg=24):
    """Color each edge with a GRADIENT from its own street_start_length_m
    to street_end_length_m, not one flat color. Each synthetic edge here is
    a single straight OSM way with no intermediate nodes, so a flat color
    keyed to only the start (or only the end) bound would make every arm
    of a radial network look identically dark (they all start at 0 at the
    origin), hiding the entire "numbers climb along the street" story this
    figure exists to show.
    """
    all_dists = np.concatenate([
        edges_gdf["street_start_length_m"].dropna().to_numpy(),
        edges_gdf["street_end_length_m"].dropna().to_numpy(),
    ])
    vmax = all_dists.max() if len(all_dists) else 1.0
    cmap = matplotlib.colormaps[cmap_name]
    seen = set()
    for (u, v, k), row in edges_gdf.iterrows():
        if (v, u, k) in seen or row.geometry is None:
            continue
        seen.add((u, v, k))
        d0, d1 = row.get("street_start_length_m"), row.get("street_end_length_m")
        xs, ys = np.asarray(row.geometry.xy[0]), np.asarray(row.geometry.xy[1])
        if d0 is None or (isinstance(d0, float) and np.isnan(d0)):
            ax.plot(xs, ys, color="#bbbbbb", lw=4, solid_capstyle="round", zorder=2)
            continue
        # Interpolate along the line's own parametric position (0..1),
        # not just its two raw vertices, so the gradient is smooth even
        # if the geometry has intermediate vertices.
        t = np.linspace(0, 1, n_seg + 1)
        xs_i = np.interp(t, np.linspace(0, 1, len(xs)), xs)
        ys_i = np.interp(t, np.linspace(0, 1, len(ys)), ys)
        dists = d0 + t * (d1 - d0)
        for i in range(n_seg):
            ax.plot(xs_i[i:i + 2], ys_i[i:i + 2], color=cmap(dists[i] / vmax), lw=4,
                     solid_capstyle="round", zorder=2)
    return vmax, cmap


def annotate_house_numbers_radial(ax, edges_gdf, ids, tips):
    """Drop a few synthetic building points offset to either side of the
    O-N arm and label them with the house number assign_house_numbers /
    numbering._side_of_line would give them, to make the even-right /
    odd-left convention concrete."""
    from osmaddresssystem.numbering import _side_of_line, _house_number_from_distance

    o = np.array([0.0, 0.0])
    n = np.array(tips["N"])
    direction = (n - o) / np.linalg.norm(n - o)
    normal = np.array([-direction[1], direction[0]])
    line = LineString([tuple(o), tuple(n)])
    for frac in (0.15, 0.45, 0.75):
        base = o + frac * (n - o)
        for side_sign, side_label in ((1, "left"), (-1, "right")):
            pt = base + side_sign * 14 * normal
            side = _side_of_line(Point(pt), line)
            dist_m = frac * np.linalg.norm(n - o)
            house_no = _house_number_from_distance(dist_m, 20.0, side)
            ax.scatter(*pt, color="black", s=14, zorder=5)
            ax.annotate(str(house_no), pt, fontsize=7.5, xytext=(pt[0] + 6, pt[1] + 6))


def main() -> None:
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(12.5, 6.6))

    # --- Panel 0: radial numbering --------------------------------------
    edges_gdf, streets_df, ids, tips = run_radial()
    vmax, cmap = plot_edges_by_distance(ax0, edges_gdf)
    ax0.scatter([0], [0], color="red", marker="x", s=90, zorder=6, label="origin (numbering starts here)")
    for label, (x, y) in tips.items():
        ax0.annotate(label, (x, y), fontsize=10, fontweight="bold", xytext=(x * 1.08, y * 1.08))
    annotate_house_numbers_radial(ax0, edges_gdf, ids, tips)
    sm = cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, vmax))
    cbar = fig.colorbar(sm, ax=ax0, fraction=0.046, pad=0.04)
    cbar.set_label("cumulative distance from origin (m)")
    ax0.set_title("(a) Radial numbering: O-N/E/S/W climb\noutward from the origin, O-N-E chord is\nclassified transversal (numbers climb clockwise)")
    ax0.set_aspect("equal")
    ax0.set_xlabel("x (m)")
    ax0.set_ylabel("y (m)")
    ax0.legend(loc="lower left", fontsize=7)

    # --- Panel 1: grid numbering -----------------------------------------
    edges_gdf_g, streets_df_g = run_grid()
    vmax_g, cmap_g = plot_edges_by_distance(ax1, edges_gdf_g)
    xmax = max(edges_gdf_g.geometry.apply(lambda g: g.centroid.x)) if len(edges_gdf_g) else 300
    ymax = max(edges_gdf_g.geometry.apply(lambda g: g.centroid.y)) if len(edges_gdf_g) else 300
    ax1.annotate(
        "", xy=(0, ymax * 1.15), xytext=(0, -30),
        arrowprops=dict(arrowstyle="->", color="black", lw=1.6),
    )
    ax1.annotate("numbers climb\n(avenues, N)", (10, ymax * 1.0), fontsize=8)
    ax1.annotate(
        "", xy=(xmax * 1.15, 0), xytext=(-30, 0),
        arrowprops=dict(arrowstyle="->", color="black", lw=1.6),
    )
    ax1.annotate("numbers climb\n(streets, E)", (xmax * 0.75, 15), fontsize=8)
    sm_g = cm.ScalarMappable(cmap=cmap_g, norm=plt.Normalize(0, vmax_g))
    cbar_g = fig.colorbar(sm_g, ax=ax1, fraction=0.046, pad=0.04)
    cbar_g.set_label("cumulative distance along its own axis (m)")
    ax1.set_title("(b) Grid numbering (direction_deg=0): avenues\nnumbered climbing north, cross streets\nnumbered climbing east, independent of any center")
    ax1.set_aspect("equal")
    ax1.set_xlabel("x (m)")
    ax1.set_ylabel("y (m)")

    fig.suptitle(
        "House-number assignment (street_direction.assign_street_directionality + numbering.assign_house_numbers)\n"
        "run on synthetic radial and grid street networks",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=180, pil_kwargs={"quality": 92})
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
