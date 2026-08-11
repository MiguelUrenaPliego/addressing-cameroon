"""Build interactive survey maps for the Douala and Fotouni (Bandja) sites.

Combines each site's own OSM-derived street network (streets.gpkg, from
OSMAddressSystem/experiments/<Douala|Bandja>) with the ground-truth survey
points in data/douala/douala.csv and data/fotouni/fotouni.csv.

Two kinds of map are produced, both self-contained (no server needed) and
sharing one visual language (same street colors/weights, same legend
component):

  - The plain click-to-geocode map (OSMAddressSystem's own
    geocode_map.build_interactive_geocode_map, reused as-is for the
    click-anywhere behavior) - regenerated in place for every subregion
    under OSMAddressSystem/experiments/<site>/**/geocode_map.html, with
    thicker streets and a legend added.
  - The survey map - every surveyed building (any point whose landuse is
    not "road") drawn as an emoji marker (one emoji per landuse, using the
    FIRST landuse in the list). Clicking a building shows a popup with:
      - its reverse-geocoded address, with a red dot on the street marking
        exactly where that address projects to,
      - every other survey field EXCEPT coordinates (id, landuse,
        products, timestamps, precisions, mobile_error, remarks),
      - its photo(s), if any (Fotouni only) - click a photo to view it
        fullscreen,
      - two circles with a center dot: one at the RTK fix (radius =
        rtk_precision, green), one at the mobile-phone fix (radius =
        mobile_precision, blue).
    Written to data/douala/survey_map.html and data/fotouni/survey_map.html
    so the map travels with the dataset; the site's own images/ folder is
    referenced with a relative path, and the combined streets.gpkg used to
    build the map is copied alongside it for reproducibility.

Run with the OSMAddressSystem project's own venv, e.g.:
    OSMAddressSystem/.venv/bin/python3 data/scripts/build_survey_maps.py
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

import geopandas as gpd
import pandas as pd
from branca.element import Element
from shapely.geometry import Point

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OSM_ROOT = os.path.join(REPO_ROOT, "OSMAddressSystem")
sys.path.insert(0, OSM_ROOT)

import folium  # noqa: E402
from osmaddresssystem import geocode_map  # noqa: E402
from osmaddresssystem import street_editor  # noqa: E402
from osmaddresssystem.numbering import _project_point_onto_street  # noqa: E402
from osmaddresssystem.default_values import DEFAULT_NUMBER_DIVISOR_M  # noqa: E402

LIST_SEP = "|"

# Google's own hybrid tiles stop natively at zoom ~20-21; Leaflet upscales
# them for any zoom above that (maxNativeZoom) up to the map/layer's own
# maxZoom - so "max zoom 28, or the max available upscaled" just means
# maxZoom=28 with maxNativeZoom left at the tiles' real native maximum.
_MAX_ZOOM = 28
_MAX_NATIVE_ZOOM = 20

# Default view for Fotouni's own address/survey maps: the town's main
# intersection, not whatever the streets/buildings bounding box computes to.
FOTOUNI_DEFAULT_CENTER = (5.321098, 10.227436)
FOTOUNI_DEFAULT_ZOOM = 17


def _new_map_max_zoom(location, zoom_start):
    """Wraps street_editor._new_map to raise both the map's own maxZoom and
    the Google Hybrid tile layer's maxZoom/maxNativeZoom to _MAX_ZOOM, so
    every map this script produces can be zoomed in far past the tiles'
    native resolution (Leaflet just upscales the last available tile)."""
    fmap = folium.Map(location=location, zoom_start=zoom_start, tiles=None, max_zoom=_MAX_ZOOM)
    folium.TileLayer(
        tiles=street_editor._GOOGLE_HYBRID_TILE_URL,
        attr="Google Hybrid",
        name="Google Hybrid",
        opacity=0.6,
        overlay=False,
        control=False,
        max_zoom=_MAX_ZOOM,
        max_native_zoom=_MAX_NATIVE_ZOOM,
    ).add_to(fmap)
    return fmap


geocode_map._new_map = _new_map_max_zoom
street_editor._new_map = _new_map_max_zoom

# One emoji per landuse value. The FIRST tag in a point's landuse list
# picks the emoji, per the task's own "always use list first" rule.
LANDUSE_EMOJI = {
    "residential": "\U0001F3E0",       # house
    "commercial": "\U0001F6D2",        # shopping cart
    "office": "\U0001F3E2",            # office building
    "industry": "\U0001F3ED",          # factory
    "public_service": "\U0001F3DB️",  # classical building
    "road": "\U0001F6E3️",        # road (not used for markers, kept for completeness)
}
DEFAULT_EMOJI = "\U0001F4CD"  # pin, fallback for an unrecognized/missing landuse

# Streets thicker than OSMAddressSystem's own defaults (see
# geocode_map._RANK_STYLE), especially rank 4 (residential/service/
# unclassified) and rank 5 (footway/path) which are hard to make out at the
# library's default 1-2px weight against the satellite basemap. Shared by
# every map this script produces (geocode-only and survey maps alike).
_THICKER_RANK_STYLE = {
    # The old orange/amber/pale-yellow progression for primary/secondary/
    # tertiary was too close in hue to tell apart at a glance (especially
    # against satellite imagery). These use clearly distinct hues instead
    # (red, orange, yellow, purple) rather than shades of the same color.
    0: {"color": "#e31a1c", "weight": 8, "label": "Motorway / trunk"},
    1: {"color": "#ff7f00", "weight": 7, "label": "Primary"},
    2: {"color": "#ffe600", "weight": 6, "label": "Secondary"},
    3: {"color": "#9b59b6", "weight": 5, "label": "Tertiary"},
    4: {"color": "#ffffff", "weight": 6, "label": "Residential / unclassified / service"},
    5: {"color": "#00e5ff", "weight": 5, "label": "Footway / path"},
}
_FALLBACK_RANK_STYLE = {"color": "#ffffff", "weight": 5}


def _apply_thicker_street_style() -> None:
    """Overwrite geocode_map's own rank->style table in place, so the
    reused ``build_interactive_geocode_map`` draws thicker streets without
    duplicating its rendering logic here."""
    geocode_map._RANK_STYLE.clear()
    geocode_map._RANK_STYLE.update(_THICKER_RANK_STYLE)
    geocode_map._FALLBACK_RANK_STYLE.clear()
    geocode_map._FALLBACK_RANK_STYLE.update(_FALLBACK_RANK_STYLE)


def _legend_html(include_buildings: bool, numbering_mode: str | None = None) -> str:
    """A fixed-position legend: street rank colors always; landuse emoji,
    the red address-projection dot, and the RTK/mobile accuracy circles
    only on the survey map (``include_buildings``), since a plain
    geocode-only map has none of those; the numbering-reference marker
    (radial origin "X" or grid direction arrows) whenever ``numbering_mode``
    is given, since every map (geocode-only and survey alike) draws one."""
    street_rows = "".join(
        '<div><span style="display:inline-block;width:18px;height:4px;'
        'background:{color};border:1px solid #000;vertical-align:middle;'
        'margin-right:6px;"></span>'
        "{label}</div>".format(color=s["color"], label=s["label"])
        for s in _THICKER_RANK_STYLE.values()
    )
    buildings_rows = ""
    if include_buildings:
        emoji_rows = "".join(
            "<div>{emoji} {label}</div>".format(emoji=emoji, label=landuse.replace("_", " "))
            for landuse, emoji in LANDUSE_EMOJI.items()
        )
        buildings_rows = f"""
<hr style="margin:6px 0;">
{emoji_rows}
<hr style="margin:6px 0;">
<div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;
background:#ff0000;vertical-align:middle;margin-right:6px;"></span>Address projected onto street</div>
<div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;
border:2px solid #00c853;vertical-align:middle;margin-right:6px;"></span>RTK fix + accuracy circle</div>
<div><span style="display:inline-block;width:10px;height:10px;border-radius:50%;
border:2px solid #2979ff;vertical-align:middle;margin-right:6px;"></span>Mobile fix + accuracy circle</div>
<div><span style="color:#ff69b4;font-weight:bold;">&#8593;</span> Photo location + camera bearing</div>
"""
    numbering_row = ""
    if numbering_mode == "radial":
        numbering_row = ('<hr style="margin:6px 0;"><div><span style="color:#ff0000;'
                          'font-weight:bold;">&times;</span> Radial numbering origin</div>')
    # Grid mode's own explanation lives on the direction-arrows widget
    # itself (bottom-right corner) instead of a legend row - see
    # _grid_direction_widget_html.
    return f"""
<div id="osmLegend" style="position: fixed; top: 20px; right: 20px; z-index: 9999;
     background: white; color: #111; padding: 10px 12px; border: 1px solid #333;
     border-radius: 4px; font-size: 12px; line-height: 1.5; max-width: 260px;">
    <b>Legend</b>
    <hr style="margin:6px 0;">
    <label style="cursor:pointer;">
        <input type="checkbox" id="osmStreetLabelsCheckbox"> Show street names
    </label>
    <hr style="margin:6px 0;">
    {street_rows}
    {buildings_rows}
    {numbering_row}
</div>
"""


def _load_combined_streets(experiment_dir: str) -> gpd.GeoDataFrame:
    """Every ``streets.gpkg`` found under ``experiment_dir`` (one per
    subregion), concatenated into a single GeoDataFrame in a shared
    projected CRS - a site can have more than one generated subregion, and
    the geocoder/renderer should cover all of them."""
    paths = sorted(glob.glob(os.path.join(experiment_dir, "**", "streets.gpkg"), recursive=True))
    if not paths:
        raise FileNotFoundError(f"No streets.gpkg found under {experiment_dir!r}")
    parts = [gpd.read_file(p) for p in paths]
    crs = parts[0].crs
    parts = [p.to_crs(crs) if p.crs != crs else p for p in parts]
    combined = pd.concat(parts, ignore_index=True)
    combined["street_id"] = range(1, len(combined) + 1)  # keep ids unique across subregions
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=crs)


def _compute_numbering_extras(experiment_dir: str, streets_gdf: gpd.GeoDataFrame, numbering_mode: str):
    """The origin (radial mode) or direction_deg (grid mode) needed to draw
    geocode_map's own red-X / red-arrows numbering-reference markers.
    Neither is persisted anywhere by the original pipeline (both are only
    ever passed transiently to build_interactive_geocode_map at generation
    time), so they are recomputed here from what IS on disk:

    - radial (Bandja): the origin used by the pipeline is simply each
      subregion's own polygon centroid - since that polygon itself isn't
      saved, this approximates it as the centroid of the combined streets'
      own geometry, which is visually equivalent for a "here's roughly the
      numbering center" marker.
    - grid (Douala): direction_deg is recomputed exactly, via
      street_direction.detect_main_direction on the combined edges.gpkg
      (same edges the original pipeline measured it from).

    Returns:
        ``(origin, direction_deg)`` - whichever one ``numbering_mode``
        doesn't need is ``None``.
    """
    if numbering_mode == "radial":
        centroid = streets_gdf.geometry.union_all().centroid
        return (centroid.x, centroid.y), None
    if numbering_mode == "grid":
        from osmaddresssystem import street_direction

        edge_paths = sorted(glob.glob(os.path.join(experiment_dir, "**", "edges.gpkg"), recursive=True))
        if not edge_paths:
            return None, None
        parts = [gpd.read_file(p) for p in edge_paths]
        crs = streets_gdf.crs
        parts = [p.to_crs(crs) if p.crs != crs else p for p in parts]
        edges_gdf = pd.concat(parts, ignore_index=True)
        direction_deg = street_direction.detect_main_direction(edges_gdf)
        return None, direction_deg
    return None, None


def _split_list(value) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return []
    return [v for v in str(value).split(LIST_SEP) if v]


def _clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _round2(value):
    """Round a numeric value to 2 decimal places; passes through None."""
    value = _clean(value)
    if value is None:
        return None
    return round(float(value), 2)


def _project_for_address(point: Point, streets_gdf: gpd.GeoDataFrame, number_divisor_m: float):
    """Nearest street, reverse-geocoded address string, and the point ON
    that street the input projects to (for the red "projection dot"),
    mirroring ``numbering.geocode_points``/``_project_point_onto_street``
    but also returning the projected geometry, which those functions don't
    expose."""
    sindex = streets_gdf.sindex
    nearest_pos = sindex.nearest(point, return_all=False)[1][0]
    street_row = streets_gdf.iloc[nearest_pos]

    # A street can be a MultiLineString (a real gap left by a subregion
    # clip - see pipeline.streets_gdf_from_edges); _project_point_onto_street
    # (and _side_of_line underneath it) only accepts a single LineString, so
    # project onto whichever part is actually nearest to the point.
    geom = street_row.geometry
    if geom.geom_type == "MultiLineString":
        geom = min(geom.geoms, key=lambda part: part.distance(point))

    distance_along, _side, house_number = _project_point_onto_street(
        point, geom, number_divisor_m
    )
    projected_point = geom.interpolate(distance_along)

    street_name = _clean(street_row.get("street_name"))
    label = street_name if street_name else f"street {street_row.get('street_id')}"
    parts = [str(house_number), label]
    for column in ("neighbourhood", "city", "province", "country"):
        value = _clean(street_row.get(column)) if column in streets_gdf.columns else None
        if value:
            parts.append(str(value))
    address = ", ".join(parts)
    return address, projected_point


def _building_records(df: pd.DataFrame, streets_gdf: gpd.GeoDataFrame, images_rel_dir: str) -> list[dict]:
    """One dict per survey point with a usable position, ready to embed as
    JSON: marker position, emoji, popup fields (everything except
    coordinates), precomputed address + street-projection point, the
    RTK/mobile positions + precisions for the two accuracy circles
    (positions are needed to place the circles, even though they're not
    shown as text), the photo's own position/bearing (for the photo-arrow
    marker), and any photo(s) as paths relative to the html file's own
    folder. Every row with an RTK or mobile position is included - roads
    included, not just buildings - so the map never hides a surveyed point
    that has a position."""
    crs = streets_gdf.crs
    records = []
    for _, row in df.iterrows():
        landuse_list = _split_list(row.get("landuse"))

        rtk_lat, rtk_lon = _clean(row.get("rtk_lat")), _clean(row.get("rtk_lon"))
        mob_lat, mob_lon = _clean(row.get("mobile_lat")), _clean(row.get("mobile_lon"))
        marker_lat, marker_lon = (rtk_lat, rtk_lon) if rtk_lat is not None else (mob_lat, mob_lon)
        if marker_lat is None:
            continue  # no usable position at all

        marker_point_proj = gpd.GeoSeries([Point(marker_lon, marker_lat)], crs=4326).to_crs(crs).iloc[0]
        address, projected_point = _project_for_address(
            marker_point_proj, streets_gdf, DEFAULT_NUMBER_DIVISOR_M
        )
        proj_wgs84 = gpd.GeoSeries([projected_point], crs=crs).to_crs(4326).iloc[0]

        emoji = LANDUSE_EMOJI.get(landuse_list[0], DEFAULT_EMOJI) if landuse_list else DEFAULT_EMOJI

        photos = [images_rel_dir + "/" + p for p in _split_list(row.get("photo"))] if "photo" in df.columns else []

        # One timestamp per point: prefer the RTK capture time (matches the
        # marker's own position preference above), falling back to the
        # mobile capture time when there's no RTK reading at all.
        timestamp = _clean(row.get("rtk_timestamp")) or _clean(row.get("mobile_timestamp"))

        records.append(dict(
            id=int(row["id"]),
            lat=marker_lat, lon=marker_lon,
            emoji=emoji,
            landuse=landuse_list,
            products=_split_list(row.get("products")),
            timestamp=timestamp,
            comments_landuse=_clean(row.get("comments_landuse")),
            comments_gnss=_clean(row.get("comments_gnss")),
            rtk_lat=rtk_lat, rtk_lon=rtk_lon, rtk_precision=_round2(row.get("rtk_precision")),
            mobile_lat=mob_lat, mobile_lon=mob_lon, mobile_precision=_round2(row.get("mobile_precision")),
            mobile_error=_round2(row.get("mobile_error")),
            photo_precision=_round2(row.get("photo_precision")) if "photo_precision" in df.columns else None,
            photo_bearing=_round2(row.get("photo_bearing")) if "photo_bearing" in df.columns else None,
            photo_lat=_clean(row.get("photo_lat")) if "photo_lat" in df.columns else None,
            photo_lon=_clean(row.get("photo_lon")) if "photo_lon" in df.columns else None,
            photos=photos,
            address=address,
            proj_lat=proj_wgs84.y, proj_lon=proj_wgs84.x,
        ))
    return records


def _buildings_js(buildings: list[dict]) -> str:
    """The building-marker layer's own JS: one emoji DivIcon marker per
    building, and a click handler that reuses the same red
    "projection dot" + result box the anywhere-click geocoder uses (see
    geocode_map._geocode_map_js), plus the two RTK/mobile accuracy circles
    (each with a small center dot), the popup's non-coordinate fields, and
    a click-to-fullscreen viewer for any attached photo(s)."""
    return f"""
<script>
window.__surveyBuildings = {json.dumps(buildings)};

function osmSurveyFieldRow(label, value) {{
    if (value === null || value === undefined || value === "") {{ return ""; }}
    return "<b>" + label + ":</b> " + value + "<br>";
}}

function osmSurveyOpenFullscreen(src) {{
    var overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;"
        + "background:rgba(0,0,0,0.9);z-index:99999;display:flex;"
        + "align-items:center;justify-content:center;cursor:zoom-out;";
    var img = document.createElement("img");
    img.src = src;
    img.style.cssText = "max-width:95%;max-height:95%;box-shadow:0 0 20px #000;";
    overlay.appendChild(img);
    overlay.addEventListener("click", function() {{ document.body.removeChild(overlay); }});
    document.body.appendChild(overlay);
}}

function osmSurveyToggleComment(id) {{
    var el = document.getElementById("osmSurveyComment_" + id);
    if (el) {{ el.style.display = (el.style.display === "none") ? "block" : "none"; }}
}}

function osmSurveyPopupHtml(b) {{
    var html = "";
    html += "<b>Address:</b> " + b.address + "<br>";
    html += osmSurveyFieldRow("Id", b.id);
    html += osmSurveyFieldRow("Landuse", b.landuse.join(", "));
    html += osmSurveyFieldRow("Products", b.products.join(", "));
    html += osmSurveyFieldRow("Timestamp", b.timestamp);
    html += osmSurveyFieldRow("RTK precision (m)", b.rtk_precision);
    html += osmSurveyFieldRow("Mobile precision (m)", b.mobile_precision);
    html += osmSurveyFieldRow("Mobile error vs RTK (m)", b.mobile_error);
    html += osmSurveyFieldRow("Photo bearing (deg from north)", b.photo_bearing);
    html += osmSurveyFieldRow("Photo GPS precision (m)", b.photo_precision);

    var comments = [b.comments_landuse, b.comments_gnss].filter(function(c) {{
        return c !== null && c !== undefined && c !== "";
    }});
    if (comments.length > 0) {{
        html += '<span onclick="osmSurveyToggleComment(' + b.id + ')" '
            + 'title="Show remarks" style="cursor:pointer;font-size:18px;">\\u{{1F4AC}}</span><br>';
        html += '<div id="osmSurveyComment_' + b.id + '" style="display:none;margin-top:4px;">';
        html += comments.join("<br>");
        html += "</div>";
    }}

    if (b.photos && b.photos.length > 0) {{
        html += "<div style='margin-top:6px;'>";
        b.photos.forEach(function(src) {{
            html += "<img src='" + src + "' style='width:120px;height:auto;margin:2px;"
                + "cursor:zoom-in;border:1px solid #333;' "
                + "onclick='osmSurveyOpenFullscreen(&quot;" + src + "&quot;)'>";
        }});
        html += "</div>";
    }}
    return html;
}}

function osmSurveyClearBuildingOverlays(map) {{
    // Just the survey-specific overlays (a building's accuracy circles/dots
    // and photo arrows) - NOT the plain geocode click's own address
    // marker/projection dot, which the library manages itself.
    if (window.__surveyRtkCircle) {{ map.removeLayer(window.__surveyRtkCircle); window.__surveyRtkCircle = null; }}
    if (window.__surveyRtkDot) {{ map.removeLayer(window.__surveyRtkDot); window.__surveyRtkDot = null; }}
    if (window.__surveyMobileCircle) {{ map.removeLayer(window.__surveyMobileCircle); window.__surveyMobileCircle = null; }}
    if (window.__surveyMobileDot) {{ map.removeLayer(window.__surveyMobileDot); window.__surveyMobileDot = null; }}
    if (window.__surveyPhotoArrows) {{
        window.__surveyPhotoArrows.forEach(function(m) {{ map.removeLayer(m); }});
    }}
    window.__surveyPhotoArrows = [];
}}

function osmSurveyClearOverlays(map) {{
    // Everything: a building marker's own click needs to remove any
    // previous building's (or the plain-geocode click's) address
    // marker/projection dot too, before drawing its own.
    if (window.__geocodeMarker) {{ map.removeLayer(window.__geocodeMarker); window.__geocodeMarker = null; }}
    if (window.__geocodeProjectionMarker) {{ map.removeLayer(window.__geocodeProjectionMarker); window.__geocodeProjectionMarker = null; }}
    osmSurveyClearBuildingOverlays(map);
}}

document.addEventListener("DOMContentLoaded", function() {{
    var map = window["__OSM_GEOCODE_MAP_VAR__"];

    // A plain map click (not on a building marker - marker clicks don't
    // bubble up to the map's own click event) should still close any
    // building's popup box and clear its circles/dots/photo arrows, even
    // though that click isn't on a building itself. The library's own
    // click handler (bound earlier, since its <script> is embedded before
    // this one) runs first and draws its own address marker; this listener
    // clears the survey-specific overlays right after, so nothing from the
    // last building lingers alongside the new plain-click result.
    map.on("click", function() {{ osmSurveyClearBuildingOverlays(map); }});

    window.__surveyBuildings.forEach(function(b) {{
        var icon = L.divIcon({{
            html: '<div style="width: 26px; height: 26px; border-radius: 50%; background: #ffffff; '
                + 'border: 2px solid #333; box-shadow: 0 1px 3px rgba(0,0,0,0.6); '
                + 'display: flex; align-items: center; justify-content: center; '
                + 'font-size: 15px; line-height: 1; white-space: nowrap; transform: translate(-50%, -50%);">'
                + b.emoji + '</div>',
            className: "", iconSize: [26, 26], iconAnchor: [13, 13]
        }});
        var marker = L.marker([b.lat, b.lon], {{icon: icon}}).addTo(map);

        marker.on("click", function() {{
            var box = document.getElementById("osmGeocodeResultBox");
            if (box) {{ box.innerHTML = osmSurveyPopupHtml(b); }}
            var controls = document.getElementById("osmGeocodeControls");
            if (controls) {{ controls.style.display = "block"; }}

            osmSurveyClearOverlays(map);

            window.__geocodeProjectionMarker = L.circleMarker([b.proj_lat, b.proj_lon], {{
                radius: 6, color: "#ff0000", fillColor: "#ff0000", fillOpacity: 1, weight: 2
            }}).addTo(map).bindPopup(b.address).openPopup();

            if (b.rtk_lat !== null && b.rtk_precision !== null) {{
                window.__surveyRtkCircle = L.circle([b.rtk_lat, b.rtk_lon], {{
                    radius: b.rtk_precision, color: "#00c853", fillColor: "#00c853",
                    fillOpacity: 0.15, weight: 2
                }}).addTo(map).bindTooltip("RTK precision: " + b.rtk_precision + " m");
                window.__surveyRtkDot = L.circleMarker([b.rtk_lat, b.rtk_lon], {{
                    radius: 4, color: "#00c853", fillColor: "#00c853", fillOpacity: 1, weight: 1
                }}).addTo(map);
            }}
            if (b.mobile_lat !== null && b.mobile_precision !== null) {{
                window.__surveyMobileCircle = L.circle([b.mobile_lat, b.mobile_lon], {{
                    radius: b.mobile_precision, color: "#2979ff", fillColor: "#2979ff",
                    fillOpacity: 0.15, weight: 2
                }}).addTo(map).bindTooltip("Mobile-phone precision: " + b.mobile_precision + " m");
                window.__surveyMobileDot = L.circleMarker([b.mobile_lat, b.mobile_lon], {{
                    radius: 4, color: "#2979ff", fillColor: "#2979ff", fillOpacity: 1, weight: 1
                }}).addTo(map);
            }}
            if (b.photo_lat !== null && b.photo_lon !== null && b.photo_bearing !== null) {{
                var arrowIcon = L.divIcon({{
                    html: '<div style="font-size: 30px; line-height: 1; color: #ff69b4; '
                        + 'text-shadow: -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000; '
                        + 'transform: translate(-50%, -50%) rotate(' + b.photo_bearing + 'deg);">&#8593;</div>',
                    className: "", iconSize: [30, 30], iconAnchor: [15, 15]
                }});
                var arrowMarker = L.marker([b.photo_lat, b.photo_lon], {{icon: arrowIcon}})
                    .addTo(map).bindTooltip("Photo location, facing " + b.photo_bearing + "\\u00b0 from north");
                window.__surveyPhotoArrows.push(arrowMarker);
            }}
        }});

        marker.bindTooltip(b.address);
    }});
}});
</script>
"""


def _close_button_js() -> str:
    """A small close ("x") button added to the top-right corner of the
    click-to-geocode result box (``osmGeocodeControls``, added by
    ``geocode_map._geocode_map_js``) - dismisses the box entirely; clicking
    the map again (or a building marker, on the survey map) brings it back."""
    return """
<script>
document.addEventListener("DOMContentLoaded", function() {
    var controls = document.getElementById("osmGeocodeControls");
    if (!controls) { return; }
    var closeBtn = document.createElement("span");
    closeBtn.innerHTML = "&times;";
    closeBtn.title = "Close";
    closeBtn.style.cssText = "position:absolute; top:2px; right:8px; cursor:pointer; "
        + "font-weight:bold; font-size:18px; color:#333; line-height:1;";
    closeBtn.addEventListener("click", function() { controls.style.display = "none"; });
    controls.appendChild(closeBtn);

    var map = window["__OSM_GEOCODE_MAP_VAR__"];
    if (map) {
        map.on("click", function() { controls.style.display = "block"; });
    }
});
</script>
"""


def _grid_direction_widget_html(direction_deg: float) -> str:
    """A fixed bottom-right compass-style widget showing grid numbering's
    two axes (``direction_deg`` and its perpendicular) as thick arrows in an
    "L" arrangement: both rooted at the diagram's own center point (like a
    vector diagram) and 90 degrees apart by construction, so they always
    form a clean right angle regardless of the actual compass direction -
    not tied to any map coordinate (unlike the library's own
    ``_draw_direction_arrows``, which anchors them at a location on the map
    itself).

    The arrow glyph is pivoted from the *center* of its own square box, not
    its bottom edge: anchoring at the bottom only leaves room for the glyph
    to swing through the upper half-circle, so any axis whose direction
    happens to point downward (e.g. a cross axis near due south) rotates
    the glyph straight through the box's bottom edge into whatever sits
    below it. Pivoting at the center instead means the tip sweeps a full
    circle around that center point, whatever ``direction_deg`` is, so it
    can never leave the box as long as the box's half-width exceeds the
    glyph's own length.
    """
    cross_deg = (direction_deg + 90) % 360
    box_size = 90  # generous relative to the ~34px glyph, so its swept tip never reaches the edge
    arrow = (
        f'<div style="position:absolute; left:50%; top:50%; transform-origin: 50% 100%; '
        'font-size: 34px; font-weight: bold; color: #ff0000; line-height: 1; '
        'text-shadow: -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000; '
        'transform: translate(-50%, -100%) rotate({deg}deg);">&#8593;</div>'
    )
    return f"""
<div id="osmGridDirectionWidget" style="position: fixed; bottom: 20px; right: 20px; z-index: 9999;
     background: white; padding: 8px 12px; border: 1px solid #333; border-radius: 4px;">
    <div style="position:relative; width:{box_size}px; height:{box_size}px;">
        {arrow.format(deg=direction_deg % 360)}
        {arrow.format(deg=cross_deg)}
    </div>
    <div style="text-align:center; font-size:11px; margin-top:4px;">Grid directions</div>
</div>
"""


def _street_labels_toggle_js() -> str:
    """Wires up the "Show street names" checkbox (part of the legend box
    itself - see ``_legend_html``) to show/hide every street name label on
    the map. The library draws each label as its own ``folium.Marker``
    DivIcon with a distinctive inline style (11px white bold text) - there's
    no separate layer/group to toggle, so this finds them by that style and
    flips their marker's own visibility directly."""
    return """
<script>
document.addEventListener("DOMContentLoaded", function() {
    var checkbox = document.getElementById("osmStreetLabelsCheckbox");
    function labelIcons() {
        return document.querySelectorAll('div[style*="font-size: 11px"]');
    }
    function applyVisibility() {
        var display = checkbox.checked ? "" : "none";
        labelIcons().forEach(function(div) {
            var marker = div.closest(".leaflet-marker-icon");
            if (marker) { marker.style.display = display; }
        });
    }
    checkbox.addEventListener("change", applyVisibility);
    // Sync once on load, since the checkbox's own default (unchecked) is
    // not how the labels are drawn on the map (always visible) - without
    // this, an unchecked box would still show every label until toggled.
    applyVisibility();
});
</script>
"""


def _regenerate_geocode_maps(experiment_dir: str, numbering_mode: str,
                              default_center: tuple[float, float] | None = None,
                              default_zoom: int | None = None) -> None:
    """Refresh every subregion's own geocode_map.html in place (thicker
    streets + legend), reusing whatever origin/streets it already has."""
    for streets_path in sorted(glob.glob(os.path.join(experiment_dir, "**", "streets.gpkg"), recursive=True)):
        subregion_dir = os.path.dirname(streets_path)
        out_path = os.path.join(subregion_dir, "geocode_map.html")
        streets_gdf = gpd.read_file(streets_path)
        origin, direction_deg = _compute_numbering_extras(subregion_dir, streets_gdf, numbering_mode)
        # direction_deg is NOT passed through here - grid mode's numbering
        # axes are shown as the fixed-position compass widget below
        # instead of geocode_map's own map-anchored arrows (see
        # _grid_direction_widget_html's docstring).
        fmap = geocode_map.build_interactive_geocode_map(
            streets_gdf, out_path, numbering_mode=numbering_mode,
            origin=origin, default_center=default_center, default_zoom=default_zoom,
        )
        fmap.get_root().html.add_child(Element(_legend_html(include_buildings=False, numbering_mode=numbering_mode)))
        close_js = _close_button_js().replace("__OSM_GEOCODE_MAP_VAR__", fmap.get_name())
        fmap.get_root().html.add_child(Element(close_js))
        fmap.get_root().html.add_child(Element(_street_labels_toggle_js()))
        if numbering_mode == "grid" and direction_deg is not None:
            fmap.get_root().html.add_child(Element(_grid_direction_widget_html(direction_deg)))
        fmap.save(out_path)
        print(f"  refreshed {out_path}")


def build_survey_map(csv_path: str, experiment_dir: str, data_dir: str, numbering_mode: str,
                      images_dir: str | None,
                      default_center: tuple[float, float] | None = None,
                      default_zoom: int | None = None) -> None:
    site_name = os.path.basename(data_dir)
    print(f"Building survey map for {site_name}")
    _apply_thicker_street_style()

    streets_gdf = _load_combined_streets(experiment_dir)

    # Copy the combined streets network into the dataset's own folder, so
    # the map's own inputs travel with the data (self-contained, per-site
    # reproducibility), not just inside OSMAddressSystem/experiments.
    streets_copy_path = os.path.join(data_dir, "streets.gpkg")
    streets_gdf.to_file(streets_copy_path, driver="GPKG")
    print(f"  copied combined streets network to {streets_copy_path}")

    df = pd.read_csv(csv_path)
    images_rel_dir = os.path.basename(images_dir) if images_dir else "images"
    buildings = _building_records(df, streets_gdf, images_rel_dir)
    print(f"  {len(buildings)} point(s) with a usable position")

    origin, direction_deg = _compute_numbering_extras(experiment_dir, streets_gdf, numbering_mode)
    out_html_path = os.path.join(data_dir, "survey_map.html")
    # direction_deg is NOT passed through here - see the equivalent comment
    # in _regenerate_geocode_maps.
    fmap = geocode_map.build_interactive_geocode_map(
        streets_gdf, out_html_path, numbering_mode=numbering_mode,
        origin=origin, default_center=default_center, default_zoom=default_zoom,
    )

    buildings_js = _buildings_js(buildings).replace("__OSM_GEOCODE_MAP_VAR__", fmap.get_name())
    fmap.get_root().html.add_child(Element(buildings_js))
    fmap.get_root().html.add_child(Element(_legend_html(include_buildings=True, numbering_mode=numbering_mode)))
    close_js = _close_button_js().replace("__OSM_GEOCODE_MAP_VAR__", fmap.get_name())
    fmap.get_root().html.add_child(Element(close_js))
    fmap.get_root().html.add_child(Element(_street_labels_toggle_js()))
    if numbering_mode == "grid" and direction_deg is not None:
        fmap.get_root().html.add_child(Element(_grid_direction_widget_html(direction_deg)))

    # The combined streets network can span a whole subregion/town, much
    # bigger than the actual surveyed area - opening the map framed on the
    # full street extent buries the buildings in a mostly-empty view (and,
    # at low zoom, can make it look like only a handful of markers exist).
    # Frame on the buildings' own bounding box instead, when there are any -
    # unless an explicit default_center was requested, which should win.
    if buildings and default_center is None:
        lats = [b["lat"] for b in buildings]
        lons = [b["lon"] for b in buildings]
        bounds_js = f"""
<script>
document.addEventListener("DOMContentLoaded", function() {{
    var map = window["{fmap.get_name()}"];
    map.fitBounds([[{min(lats)}, {min(lons)}], [{max(lats)}, {max(lons)}]], {{padding: [30, 30]}});
}});
</script>
"""
        fmap.get_root().html.add_child(Element(bounds_js))

    fmap.save(out_html_path)
    print(f"  wrote {out_html_path}")

    _regenerate_geocode_maps(experiment_dir, numbering_mode, default_center=default_center, default_zoom=default_zoom)


SITES = [
    dict(
        csv_path=os.path.join(REPO_ROOT, "data", "douala", "douala.csv"),
        experiment_dir=os.path.join(OSM_ROOT, "experiments", "Douala"),
        data_dir=os.path.join(REPO_ROOT, "data", "douala"),
        numbering_mode="grid",
        images_dir=None,
    ),
    dict(
        csv_path=os.path.join(REPO_ROOT, "data", "fotouni", "fotouni.csv"),
        experiment_dir=os.path.join(OSM_ROOT, "experiments", "Bandja"),
        data_dir=os.path.join(REPO_ROOT, "data", "fotouni"),
        numbering_mode="radial",
        images_dir=os.path.join(REPO_ROOT, "data", "fotouni", "images"),
        default_center=FOTOUNI_DEFAULT_CENTER,
        default_zoom=FOTOUNI_DEFAULT_ZOOM,
    ),
]


def main():
    # Stale copies from before survey maps moved under data/<site>/.
    for stale in ("Douala", "Bandja"):
        stale_path = os.path.join(OSM_ROOT, "experiments", stale, "survey_map.html")
        if os.path.exists(stale_path):
            os.remove(stale_path)

    for site in SITES:
        build_survey_map(**site)

    from build_survey_index import main as build_survey_index_main

    build_survey_index_main()


if __name__ == "__main__":
    main()
