"""Folium map rendering: one polyline per road segment, colored by class,
with a click popup showing the segment's own roughness-score trace against
the actual class threshold bands used to classify it — the same metric and
the same cut points that decided the class, so the chart and the class
label always agree.
"""

from __future__ import annotations

import base64
import io
import json

import folium
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from imu_road_quality.classification import CLASS_COLORS, CLASS_ORDER, RoadClass
from imu_road_quality.segments import Segment

THIS_EDGE_COLOR = "#000000"


def _non_overlapping_bands(
    present_classes: list[RoadClass], score_bands: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    """Clip adjacent classes' score bands (already ordered by severity)
    so they never overlap: any overlap is split at its midpoint. Avoids
    semi-transparent fills blending into extra, meaningless colors.
    """
    bands = [list(score_bands[c.value]) for c in present_classes]
    for i in range(len(bands) - 1):
        if bands[i][1] > bands[i + 1][0]:
            mid = (bands[i][1] + bands[i + 1][0]) / 2
            bands[i][1] = mid
            bands[i + 1][0] = mid
        bands[i][0] = min(bands[i][0], bands[i][1])
    if bands:
        bands[-1][1] = max(bands[-1][0], bands[-1][1])
    return {c.value: (b[0], b[1]) for c, b in zip(present_classes, bands)}


class _ChartRenderer:
    """Reuses a single Figure/Axes across many segments; creating and
    tearing down a fresh matplotlib Figure per popup dominates runtime
    when a drive has 1000+ segments.
    """

    def __init__(self, score_bands: dict[str, tuple[float, float]]) -> None:
        self.present_classes = [c for c in CLASS_ORDER if c.value in score_bands]
        self.score_bands = _non_overlapping_bands(self.present_classes, score_bands)
        self.fig, self.ax = plt.subplots(figsize=(4.2, 2.5), dpi=95)

    def render(self, seg: Segment) -> str:
        ax = self.ax
        ax.clear()

        # Shaded bands = the 10th-90th percentile roughness score
        # *actually observed* among the points that were assigned to each
        # class on this drive (imu_road_quality.classification.compute_score_bands),
        # not the raw pre-merge thresholds - so a class that got merged
        # a class with zero points on this drive has no band, and every
        # band matches its class's real assigned points exactly.
        # Adjacent classes' raw percentile bands can genuinely overlap;
        # semi-transparent overlapping fills would then blend into extra
        # colors that don't correspond to any real class, so neighboring
        # bands are clipped to be non-overlapping (split at the midpoint
        # of the overlap) before drawing.
        for road_class in self.present_classes:
            low, high = self.score_bands[road_class.value]
            ax.axhspan(low, high, color=CLASS_COLORS[road_class], alpha=0.45, linewidth=0)

        if seg.point_scores:
            x = seg.point_distances if seg.point_distances else list(range(len(seg.point_scores)))
            ax.plot(x, seg.point_scores, color=THIS_EDGE_COLOR, linewidth=1.6, marker="o", markersize=3, label="this edge", zorder=10)
            # axhspan doesn't participate in x autoscaling and leaves the
            # previous segment's x-limits sticky across ax.clear() calls,
            # so the x-range must be set explicitly from this segment's
            # own data.
            x_max = max(x) if len(x) > 1 else 1.0
            margin = max(x_max * 0.05, 0.5)
            ax.set_xlim(-margin, x_max + margin)
        else:
            ax.set_xlim(0, 1)

        y_max = max(1.0, max(seg.point_scores) * 1.05) if seg.point_scores else 1.0
        ax.set_ylim(0, y_max)
        ax.set_xlabel("distance along edge (m)", fontsize=7)
        ax.set_ylabel("roughness score", fontsize=7)
        ax.tick_params(labelsize=6)

        handles = [
            plt.Line2D([0], [0], color=THIS_EDGE_COLOR, marker="o", markersize=3, linewidth=1.6, label="this edge"),
            *[
                plt.Rectangle((0, 0), 1, 1, color=CLASS_COLORS[rc], alpha=0.3, label=rc.value)
                for rc in self.present_classes
            ],
        ]
        ax.legend(handles=handles, fontsize=5.5, loc="upper right", framealpha=0.6)
        self.fig.tight_layout()

        buf = io.BytesIO()
        self.fig.savefig(buf, format="png")
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("ascii")

    def close(self) -> None:
        plt.close(self.fig)


GOOGLE_HYBRID_URL = "https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}"
GOOGLE_HYBRID_ATTR = "Google"
BASEMAP_OPACITY = 0.6
DEFAULT_LINE_WEIGHT = 5


def build_map(
    segments: list[Segment],
    score_bands: dict[str, tuple[float, float]],
    drive_name: str = "",
) -> folium.Map:
    """Build an interactive folium map: one colored polyline per segment,
    click to reveal a popup with the segment's own roughness-score trace
    plotted against each class's actual observed score band on this drive.

    The basemap defaults to Google Hybrid (satellite + labels/roads) at
    60% opacity, with a CartoDB (light) alternative also available; both
    are registered as mutually-exclusive base layers on the map's own
    ``LayerControl``, so switching between them is just picking the other
    radio button there rather than needing a separate widget.
    """
    if not segments:
        raise ValueError("No segments to plot")

    lats = [c[0] for s in segments for c in s.coords]
    lons = [c[1] for s in segments for c in s.coords]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    fmap = folium.Map(location=center, zoom_start=14, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles=GOOGLE_HYBRID_URL,
        attr=GOOGLE_HYBRID_ATTR,
        name="Google Hybrid (60% opacity)",
        overlay=False,
        control=True,
        show=True,
        opacity=BASEMAP_OPACITY,
    ).add_to(fmap)
    folium.TileLayer(
        tiles="cartodbpositron",
        name="CartoDB (light)",
        overlay=False,
        control=True,
        show=False,
    ).add_to(fmap)

    layers = {rc: folium.FeatureGroup(name=rc.value) for rc in RoadClass}
    for layer in layers.values():
        layer.add_to(fmap)

    renderer = _ChartRenderer(score_bands)
    for seg in segments:
        try:
            road_class = RoadClass(seg.road_class)
        except ValueError:
            continue
        color = CLASS_COLORS[road_class]
        png_b64 = renderer.render(seg)
        speed_kmh = seg.speed * 3.6
        popup_html = (
            f"<b>{road_class.value}</b> · score {seg.roughness_score:.2f} · {speed_kmh:.0f} km/h<br>"
            f'<img src="data:image/png;base64,{png_b64}" width="340">'
        )
        folium.PolyLine(
            locations=seg.coords,
            color=color,
            weight=DEFAULT_LINE_WEIGHT,
            opacity=0.85,
            popup=folium.Popup(popup_html, max_width=360),
            tooltip=road_class.value,
        ).add_to(layers[road_class])

    renderer.close()
    _add_legend(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    _add_line_weight_slider(fmap, [layer.get_name() for layer in layers.values()])
    return fmap


def _add_line_weight_slider(fmap: folium.Map, feature_group_names: list[str]) -> None:
    """Add a slider that rescales every road segment's polyline weight
    live, by reaching into each road-class ``FeatureGroup`` (rather than
    tracking individual ``PolyLine`` objects, which a 1000+ segment drive
    would make unwieldy) and calling Leaflet's own ``setStyle`` on every
    layer inside it."""
    names_js = json.dumps(feature_group_names)
    widget_html = f"""
    <div style="position: fixed; bottom: 30px; right: 30px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 6px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.4); font-size: 13px; width: 180px;">
        <label for="road-weight-slider"><b>Road thickness</b></label><br>
        <input type="range" id="road-weight-slider" min="1" max="12" step="1"
               value="{DEFAULT_LINE_WEIGHT}" style="width:100%">
    </div>
    <script>
    (function() {{
        var featureGroupNames = {names_js};
        var slider = document.getElementById("road-weight-slider");
        slider.addEventListener("input", function() {{
            var weight = parseInt(slider.value, 10);
            // Resolved from window[...] here, at event time, rather than
            // captured when this script tag first runs: folium emits the
            // actual `var feature_group_xxx = L.featureGroup(...)`
            // assignments in a later <script> block (the map's own), so
            // looking them up eagerly above would always find them
            // undefined and silently no-op on every slider move.
            featureGroupNames.forEach(function(name) {{
                var fg = window[name];
                if (!fg || !fg.eachLayer) return;
                fg.eachLayer(function(layer) {{
                    if (layer.setStyle) layer.setStyle({{weight: weight}});
                }});
            }});
        }});
    }})();
    </script>
    """
    fmap.get_root().html.add_child(folium.Element(widget_html))


def _add_legend(fmap: folium.Map) -> None:
    items = "".join(
        f'<i style="background:{CLASS_COLORS[rc]};width:12px;height:12px;'
        f'display:inline-block;margin-right:6px;"></i>{rc.value}<br>'
        for rc in RoadClass
    )
    legend_html = f"""
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 6px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.4); font-size: 13px;">
        <b>Road quality</b><br>{items}
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(legend_html))
