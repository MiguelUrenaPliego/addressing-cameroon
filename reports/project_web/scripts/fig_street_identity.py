"""Generate the street-identity figure by running the real
osmaddresssystem.street_ids.label_streets algorithm on a small, hand-built
synthetic intersection, designed to exercise three of its documented rules
at once:

  1. the dominant-rank tier rule (a primary road keeps one street_id straight
     through a crossing, while a lower-rank residential road is cut there)
  2. named-edge override (two residential arms sharing a real OSM name merge
     into one street_id across the same crossing, despite being cut rank)
  3. direction-based grouping at a node (collinear arms in the same bin merge)

Run with the OSMAddressSystem project's own virtualenv (networkx, shapely,
geopandas, and now matplotlib are already installed there):

    OSMAddressSystem/.venv/bin/python \
        reports/FUASProjektarbeit/scripts/fig_street_identity.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.lines import Line2D
from shapely.geometry import LineString

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "code" / "OSMAddressSystem"))

from osmaddresssystem import street_ids  # noqa: E402

OUT_PATH = REPO_ROOT / "reports" / "FUASProjektarbeit" / "figures" / "fig_street_identity.jpg"


def arm(angle_deg: float, length: float = 120.0) -> tuple[float, float]:
    rad = math.radians(angle_deg)
    return (length * math.cos(rad), length * math.sin(rad))


# osmnx-style node ids must be integers (real OSM node ids are), so the
# 6 arm endpoints and the central crossing are given integer ids, with a
# separate label map used only for the figure's own annotations.
NODE_ID = {"C": 0, "A": 1, "B": 2, "D": 3, "E": 4, "F": 5, "G": 6}
NODE_POS = {
    "C": (0.0, 0.0),
    "A": arm(180), "B": arm(0),
    "D": arm(90), "E": arm(270),
    "F": arm(150), "G": arm(-30),
}


def build_graph() -> nx.MultiDiGraph:
    """One central crossing C with six arms:

    - primary through-road, unnamed:      A(west, 180 deg) -- C -- B(east, 0 deg)
    - residential, unnamed, cut at C:     D(north, 90 deg) -- C -- E(south, 270 deg)
    - residential, both named "Rue Owendo", overriding rank:
                                           F(150 deg) -- C -- G(-30 deg)
    """
    G = nx.MultiDiGraph(crs="EPSG:3857")
    for label, (x, y) in NODE_POS.items():
        G.add_node(NODE_ID[label], x=x, y=y)

    def add(u_label, v_label, highway, name=None):
        u, v = NODE_ID[u_label], NODE_ID[v_label]
        pu, pv = NODE_POS[u_label], NODE_POS[v_label]
        fwd = LineString([pu, pv])
        bwd = LineString([pv, pu])
        osmid = u * 100 + v
        G.add_edge(u, v, 0, highway=highway, name=name, geometry=fwd, length=fwd.length, osmid=osmid)
        G.add_edge(v, u, 0, highway=highway, name=name, geometry=bwd, length=bwd.length, osmid=osmid)

    add("A", "C", "primary")
    add("C", "B", "primary")
    add("D", "C", "residential")
    add("C", "E", "residential")
    add("F", "C", "residential", name="Rue Owendo")
    add("C", "G", "residential", name="Rue Owendo")
    return G


def edge_midpoints_and_coords(edges_gdf):
    coords = {}
    for (u, v, k), row in edges_gdf.iterrows():
        if (v, u, k) in coords:
            continue
        xs, ys = row.geometry.xy
        coords[(u, v, k)] = (list(xs), list(ys))
    return coords


def main() -> None:
    G = build_graph()
    nodes_gdf, edges_gdf, streets_df = street_ids.label_streets(
        G, angle_threshold_deg=20, node_cluster_m=15.0, min_street_length_m=10.0,
    )
    print(streets_df.columns.tolist())
    print(streets_df)

    coords = edge_midpoints_and_coords(edges_gdf)

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(11.5, 7.2))

    # --- Panel 0: raw OSM input, colored by highway rank tier --------------
    rank_color = {"primary": "#c44e52", "residential": "#4c72b0"}
    seen_labels = set()
    for (u, v, k), (xs, ys) in coords.items():
        hw = G.edges[u, v, k]["highway"]
        nm = G.edges[u, v, k].get("name")
        label = f"{hw}" + (" (named)" if nm else " (unnamed)")
        color = rank_color[hw]
        ls = "-" if nm is None else "--"
        ax0.plot(xs, ys, color=color, lw=3, ls=ls, solid_capstyle="round",
                 label=label if label not in seen_labels else None)
        seen_labels.add(label)
    ax0.scatter([0], [0], color="black", zorder=5, s=40)
    ax0.annotate("node C\n(one real crossing)", (0, 0), xytext=(10, -35), fontsize=8)
    for name, (x, y) in NODE_POS.items():
        if name == "C":
            continue
        ax0.annotate(name, (x, y), fontsize=9, fontweight="bold",
                     ha="center", va="center",
                     xytext=(x * 1.12, y * 1.12))
    ax0.set_title("(a) Raw OSM input: 6 separate ways\nmeeting at one intersection")
    ax0.legend(loc="lower center", fontsize=7, bbox_to_anchor=(0.5, -0.22), ncol=2)
    ax0.set_aspect("equal")
    ax0.set_xlim(-160, 160)
    ax0.set_ylim(-160, 160)
    ax0.set_xlabel("x (m)")
    ax0.set_ylabel("y (m)")
    ax0.set_ylim(-190, 190)

    # --- Panel 1: after label_streets, colored by resulting street_id -----
    palette = ["#c44e52", "#55a868", "#dd8452", "#8172b2", "#937860", "#64b5cd"]
    sid_color = {sid: palette[i % len(palette)] for i, sid in enumerate(sorted(streets_df["street_id"].unique()))}
    for (u, v, k), (xs, ys) in coords.items():
        sid = edges_gdf.loc[(u, v, k), "street_id"]
        color = sid_color.get(sid, "#999999")
        ax1.plot(xs, ys, color=color, lw=4, solid_capstyle="round")
    ax1.scatter([0], [0], color="black", zorder=5, s=40)
    for name, (x, y) in NODE_POS.items():
        if name == "C":
            continue
        ax1.annotate(name, (x, y), fontsize=9, fontweight="bold",
                     ha="center", va="center",
                     xytext=(x * 1.12, y * 1.12))
    handles = [
        Line2D([0], [0], color=sid_color[row.street_id], lw=4,
               label=f"street_id {row.street_id}"
                     + (f" “{row.street_name}”" if row.street_name else " (unnamed)")
                     + f", {row.highway}")
        for row in streets_df.sort_values("street_id").itertuples()
    ]
    ax1.legend(handles=handles, loc="lower center", fontsize=7, bbox_to_anchor=(0.5, -0.32), ncol=1)
    ax1.set_title("(b) After label_streets: A-C-B (primary) stays one\nstreet_id, D-C / C-E (residential) are cut,\nF-C / C-G merge because they share a name")
    ax1.set_aspect("equal")
    ax1.set_xlim(-160, 160)
    ax1.set_ylim(-160, 160)
    ax1.set_xlabel("x (m)")
    ax1.set_ylabel("y (m)")
    ax1.set_ylim(-190, 190)

    fig.suptitle(
        "Street identity assignment (osmaddresssystem.street_ids.label_streets)\n"
        "run on a synthetic 6-arm intersection: dominant-tier cutting and named-edge override",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0.12, 1, 0.90))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, dpi=180, pil_kwargs={"quality": 92})
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
