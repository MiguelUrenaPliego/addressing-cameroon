"""
Build data/fotouni/fotouni.csv from the raw SW Maps xlsx/sqlite exports under
data/fotouni/raw/.

See data/building_datasets.md for full methodology.
"""
import sys
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from common import (
    parse_time, iso, haversine_m, clean_text, classify, encode_list,
    gnss_lost_comment, is_position_note, is_survey_note, sequence_align,
    is_road_remark, reject_implausible_pairs,
)

RAW = "data/fotouni/raw"
IMAGES_OUT = "data/fotouni/images"


def load_sheet(xl, sheet):
    df = xl.parse(sheet)
    rows = []
    for _, r in df.iterrows():
        rows.append(dict(
            remark_raw=r.get("Remarks"),
            time=parse_time(r.get("Time")),
            lat=r.get("Latitude"),
            lon=r.get("Longitude"),
            fix_id=r.get("Fix ID"),
            h_acc=r.get("Horizontal Accuracy"),
            bearing=r.get("Bearing"),
        ))
    return rows


def load_photos(xl):
    if "PHOTOS" not in xl.sheet_names:
        return []
    df = xl.parse("PHOTOS")
    rows = []
    for _, r in df.iterrows():
        rows.append(dict(
            time=parse_time(r.get("Time")),
            lat=r.get("Latitude"),
            lon=r.get("Longitude"),
            file=r.get("Photo File"),
        ))
    return [r for r in rows if r["time"] is not None]


def nearest_photo(t, photos, tolerance_s=90):
    if t is None or not photos:
        return None
    best = None
    best_dt = None
    for p in photos:
        dt = abs((p["time"] - t).total_seconds())
        if dt <= tolerance_s and (best_dt is None or dt < best_dt):
            best, best_dt = p, dt
    return best


def leading_number(remark):
    if remark is None or isinstance(remark, float):
        return None
    m = re.match(r"\s*(\d+)\b", str(remark))
    return int(m.group(1)) if m else None


def match_by_number_then_time(rtk_rows, phone_rows, tolerance_s=300):
    """Match two SW Maps sequences recorded during the same walk. Primary key
    is the surveyor's leading point-number (present on at least one side for
    many points); remaining rows are matched by nearest timestamp."""
    rtk_by_num = {}
    for r in rtk_rows:
        n = leading_number(r["remark_raw"])
        if n is not None:
            rtk_by_num.setdefault(n, []).append(r)
    phone_by_num = {}
    for r in phone_rows:
        n = leading_number(r["remark_raw"])
        if n is not None:
            phone_by_num.setdefault(n, []).append(r)

    # A leading-number match is only trusted if the two timestamps are
    # plausibly the same real-world moment (same walk, same day); otherwise
    # the number is a coincidental reuse (e.g. numbering restarting on a
    # different survey day) and we fall through to time-based matching.
    NUMBER_MATCH_MAX_GAP_S = 3600
    pairs = []
    used_rtk_ids = set()
    used_phone_ids = set()
    for n, rtk_list in rtk_by_num.items():
        if n in phone_by_num:
            for rr in rtk_list:
                best_pr, best_dt = None, None
                for pr in phone_by_num[n]:
                    if id(pr) in used_phone_ids:
                        continue
                    dt = abs((pr["time"] - rr["time"]).total_seconds())
                    if dt <= NUMBER_MATCH_MAX_GAP_S and (best_dt is None or dt < best_dt):
                        best_pr, best_dt = pr, dt
                if best_pr is not None:
                    pairs.append((rr, best_pr))
                    used_rtk_ids.add(id(rr))
                    used_phone_ids.add(id(best_pr))

    remaining_rtk = [r for r in rtk_rows if id(r) not in used_rtk_ids]
    remaining_phone = [r for r in phone_rows if id(r) not in used_phone_ids]
    remaining_rtk.sort(key=lambda r: r["time"])
    remaining_phone.sort(key=lambda r: r["time"])

    used_phone_ids2 = set()
    unmatched_rtk = []
    for rr in remaining_rtk:
        best, best_dt = None, None
        for pr in remaining_phone:
            if id(pr) in used_phone_ids2:
                continue
            dt = abs((pr["time"] - rr["time"]).total_seconds())
            if dt <= tolerance_s and (best_dt is None or dt < best_dt):
                best, best_dt = pr, dt
        if best is not None:
            pairs.append((rr, best))
            used_phone_ids2.add(id(best))
        else:
            unmatched_rtk.append(rr)
    unmatched_phone = [pr for pr in remaining_phone if id(pr) not in used_phone_ids2]
    return pairs, unmatched_rtk, unmatched_phone


def align(rtk_rows, phone_rows, tolerance_s=150):
    i = j = 0
    pairs, unmatched_rtk, unmatched_phone = [], [], []
    while i < len(rtk_rows) and j < len(phone_rows):
        dt = (phone_rows[j]["time"] - rtk_rows[i]["time"]).total_seconds()
        if abs(dt) <= tolerance_s:
            pairs.append((rtk_rows[i], phone_rows[j]))
            i += 1
            j += 1
        elif dt < -tolerance_s:
            unmatched_phone.append(phone_rows[j])
            j += 1
        else:
            unmatched_rtk.append(rtk_rows[i])
            i += 1
    unmatched_rtk.extend(rtk_rows[i:])
    unmatched_phone.extend(phone_rows[j:])
    return pairs, unmatched_rtk, unmatched_phone


def build_row(rtk, phone, photo_file=None, photo_lat=None, photo_lon=None,
              photo_bearing=None, extra_gnss=None):
    comments_gnss = []
    if extra_gnss:
        comments_gnss.append(extra_gnss)
    remark_source = None
    if rtk and rtk.get("remark_raw") not in (None, "") and not (isinstance(rtk.get("remark_raw"), float)):
        remark_source = rtk["remark_raw"]
    elif phone:
        remark_source = phone.get("remark_raw")
    clean, raw = clean_text(remark_source)
    landuse, products, defaulted = classify(clean)
    if clean and is_position_note(clean):
        comments_gnss.append("Position note: {}".format(clean))
    elif clean and is_survey_note(clean):
        comments_gnss.append("Field survey note: {}".format(clean))

    # A pair's OTHER side may carry its own position/survey-note remark
    # even when it wasn't picked as the primary description above (e.g. an
    # RTK point paired with a phone point whose own remark says "already
    # taken so delete") - check it too, so this kind of instruction is
    # never silently lost just because the other side had a usable
    # landuse description.
    other_source = phone.get("remark_raw") if (rtk and remark_source is rtk.get("remark_raw") and phone) else \
        (rtk.get("remark_raw") if (phone and remark_source is phone.get("remark_raw") and rtk) else None)
    if other_source is not None:
        other_clean, _ = clean_text(other_source)
        if other_clean and other_clean != clean:
            if is_position_note(other_clean):
                comments_gnss.append("Position note: {}".format(other_clean))
            elif is_survey_note(other_clean):
                comments_gnss.append("Field survey note: {}".format(other_clean))

    rtk_lat = rtk_lon = rtk_ts = rtk_prec = None
    if rtk:
        rtk_lat, rtk_lon = rtk["lat"], rtk["lon"]
        rtk_ts = iso(rtk["time"])
        rtk_prec = rtk["h_acc"]
        lost = gnss_lost_comment(rtk["fix_id"])
        if lost:
            comments_gnss.append(lost)
        if photo_bearing is None and rtk.get("bearing"):
            try:
                if float(rtk["bearing"]) != 0:
                    photo_bearing = float(rtk["bearing"])
            except (TypeError, ValueError):
                pass
    else:
        comments_gnss.append("No RTK reading available for this point.")

    mob_lat = mob_lon = mob_ts = mob_prec = None
    if phone:
        mob_lat, mob_lon = phone["lat"], phone["lon"]
        mob_ts = iso(phone["time"])
        mob_prec = phone["h_acc"]
    else:
        comments_gnss.append("No mobile-phone reading available for this point.")

    mob_error = haversine_m(rtk_lat, rtk_lon, mob_lat, mob_lon) if (rtk and phone) else None

    # Always keep the cleaned remark as comments_landuse (not just when the
    # landuse was defaulted) - the landuse/products tags are a categorical
    # summary, but the remark itself often carries specific detail no
    # category captures (e.g. "Radio Fotouni" for an office/
    # professional_services point) that a reader shouldn't lose just
    # because the point was successfully classified.
    comments_landuse = ""
    if clean and not is_position_note(clean) and not is_survey_note(clean):
        comments_landuse = clean

    return dict(
        landuse=encode_list(landuse),
        products=encode_list(products),
        rtk_lat=rtk_lat, rtk_lon=rtk_lon, rtk_timestamp=rtk_ts, rtk_precision=rtk_prec,
        mobile_lat=mob_lat, mobile_lon=mob_lon, mobile_timestamp=mob_ts, mobile_precision=mob_prec,
        mobile_error=mob_error,
        comments_landuse=comments_landuse.strip(),
        comments_gnss="; ".join(comments_gnss),
        photo=encode_list([photo_file] if photo_file else []),
        photo_lat=photo_lat, photo_lon=photo_lon,
        photo_precision=None,
        photo_bearing=photo_bearing,
    )


def cluster_by_location(points, threshold_m=15.0):
    """Group points that are within ``threshold_m`` of each other (chained -
    a point joins a cluster if it's close to ANY existing member) into the
    same cluster. Used to detect the same physical location independently
    recorded by more than one of the phone-only student contributors - see
    the call site's own comment for the distance evidence behind 15 m."""
    clusters = []
    for p in points:
        target = None
        for c in clusters:
            if any(haversine_m(p["lat"], p["lon"], q["lat"], q["lon"]) <= threshold_m for q in c):
                target = c
                break
        if target is None:
            clusters.append([p])
        else:
            target.append(p)
    return clusters


def build_contributor_cluster_row(cluster, photo_files):
    """One row for a cluster of 1+ phone-only contributor observations of
    the same physical location (see ``cluster_by_location``): mean position
    (averaging independent phone fixes reduces effective error) and mean
    precision, every member's own remark classified and unioned into
    landuse/products, and every member's own photo (already copied into
    IMAGES_OUT by the caller, as ``photo_files``) attached to the one row."""
    lats = [p["lat"] for p in cluster]
    lons = [p["lon"] for p in cluster]
    lat, lon = sum(lats) / len(lats), sum(lons) / len(lons)
    t = min(p["time"] for p in cluster)
    precisions = [p["h_acc"] for p in cluster if p.get("h_acc") is not None]
    h_acc = sum(precisions) / len(precisions) if precisions else None

    landuse_all, products_all, comments_landuse_parts = [], [], []
    for p in cluster:
        clean, _raw = clean_text(p.get("remark_raw"))
        if not clean:
            continue
        lu, pr, _defaulted = classify(clean)
        landuse_all += lu
        products_all += pr
        if not is_position_note(clean) and not is_survey_note(clean) and clean not in comments_landuse_parts:
            comments_landuse_parts.append(clean)

    photo_lat = photo_lon = photo_bearing = None
    for p in cluster:
        if p.get("photo_lat") is not None and photo_lat is None:
            photo_lat, photo_lon = p["photo_lat"], p["photo_lon"]
        try:
            if photo_bearing is None and p.get("bearing") and float(p["bearing"]) != 0:
                photo_bearing = float(p["bearing"])
        except (TypeError, ValueError):
            pass

    comments_gnss = ["No RTK reading available for this point."]
    if len(cluster) > 1:
        contributors = sorted(set(p["contributor"] for p in cluster))
        comments_gnss.append(
            "This location was recorded {} times (contributors: {}); "
            "position and precision are the mean of {} phone readings.".format(
                len(cluster), ", ".join(contributors), len(cluster)
            )
        )
    if not photo_files and all(p["contributor"] == "Brice" for p in cluster):
        comments_gnss.append("No photo file found on disk for this contributor.")

    return dict(
        landuse=encode_list(landuse_all),
        products=encode_list(products_all),
        rtk_lat=None, rtk_lon=None, rtk_timestamp=None, rtk_precision=None,
        mobile_lat=lat, mobile_lon=lon, mobile_timestamp=iso(t), mobile_precision=h_acc,
        mobile_error=None,
        comments_landuse="; ".join(comments_landuse_parts),
        comments_gnss="; ".join(comments_gnss),
        photo=encode_list(photo_files),
        photo_lat=photo_lat, photo_lon=photo_lon,
        photo_precision=None,
        photo_bearing=photo_bearing,
    )


def main():
    if os.path.isdir(IMAGES_OUT):
        shutil.rmtree(IMAGES_OUT)
    os.makedirs(IMAGES_OUT, exist_ok=True)
    out_rows = []
    unmatched_photos_note = []
    next_id = 1

    # ---- 1. fotouni_rtk main walk (Layer1) matched to Fotouni phone.xlsx ----
    # Every point with a valid GNSS fix is kept, whether or not the
    # surveyor typed a remark - an untyped point is still a real location
    # the surveyor stopped at (often one only documented via its photo),
    # and per the landuse convention (common.classify) it simply defaults
    # to residential with no comment rather than being dropped.
    rtk_xl = pd.ExcelFile(os.path.join(RAW, "fotouni_rtk", "fotouni_rtk.xlsx"))
    rtk_main_named = [r for r in load_sheet(rtk_xl, "Layer1") if r["time"] is not None]
    rtk_main_named.sort(key=lambda r: r["time"])
    rtk_photos = load_photos(rtk_xl)

    phone_xl = pd.ExcelFile(os.path.join(RAW, "Fotouni phone.xlsx"))
    phone_main = [r for r in load_sheet(phone_xl, "h_tel_") if r["time"] is not None]
    phone_main.sort(key=lambda r: r["time"])

    # Pull out one-sided road/reference points before alignment - see
    # common.is_road_remark's docstring: a road marker recorded by only one
    # device would otherwise consume a slot in the sequence and throw off
    # the alignment of the real points around it.
    rtk_main_road = [r for r in rtk_main_named if is_road_remark(r["remark_raw"])]
    road_ids = {id(r) for r in rtk_main_road}
    rtk_main_named = [r for r in rtk_main_named if id(r) not in road_ids]
    phone_main_road = [r for r in phone_main if is_road_remark(r["remark_raw"])]
    road_ids = {id(r) for r in phone_main_road}
    phone_main = [r for r in phone_main if id(r) not in road_ids]

    pairs, unmatched_rtk, unmatched_phone = sequence_align(rtk_main_named, phone_main)
    # Final sanity check: reject any pair whose RTK/phone distance is
    # implausible given the phone's own precision (more than 5x its
    # reported accuracy apart) - a sign of a wrong match, not genuine phone
    # GNSS drift, however noisy the phone reading normally is on this walk.
    pairs, unmatched_rtk, unmatched_phone = reject_implausible_pairs(pairs, unmatched_rtk, unmatched_phone)
    unmatched_rtk = unmatched_rtk + rtk_main_road
    unmatched_phone = unmatched_phone + phone_main_road
    src_media = os.path.join(RAW, "fotouni_rtk", "Media")

    def emit(rtk, phone):
        nonlocal next_id
        photo_file = None
        p_lat = p_lon = p_bear = None
        anchor_time = rtk["time"] if rtk else (phone["time"] if phone else None)
        ph = nearest_photo(anchor_time, rtk_photos)
        if ph:
            src = os.path.join(src_media, ph["file"])
            if os.path.exists(src):
                dst_name = "fotouni_{}.jpg".format(next_id)
                shutil.copyfile(src, os.path.join(IMAGES_OUT, dst_name))
                photo_file = dst_name
                p_lat, p_lon = ph["lat"], ph["lon"]
        row = build_row(rtk, phone, photo_file, p_lat, p_lon, p_bear)
        row["id"] = next_id
        next_id += 1
        out_rows.append(row)

    for rtk, phone in pairs:
        emit(rtk, phone)
    for rtk in unmatched_rtk:
        emit(rtk, None)
    for phone in unmatched_phone:
        emit(None, phone)

    # ---- 2. 'others' sheet: road / path points, RTK-only ----
    others = load_sheet(rtk_xl, "others")
    for r in others:
        if r["time"] is None:
            continue
        remark = str(r["remark_raw"]).strip().lower() if r["remark_raw"] else ""
        row = build_row(r, None)
        if "path" in remark:
            row["landuse"] = "road"
            row["products"] = "footpath"
        else:
            row["landuse"] = "road"
            row["products"] = "drive"
        row["comments_landuse"] = ""
        row["id"] = next_id
        next_id += 1
        out_rows.append(row)

    # ---- 3. 'on_the_image' sheet: rtk-only points derived from photo review ----
    on_image = load_sheet(rtk_xl, "on_the_image")
    for r in on_image:
        if r["time"] is None:
            continue
        # No separate note needed for Fix ID 0 here - gnss_lost_comment()
        # (called from build_row via the RTK-side Fix ID) already states
        # that this position came from the photo's geotag.
        extra = None
        photo_file = None
        p_lat = p_lon = None
        ph = nearest_photo(r["time"], rtk_photos, tolerance_s=300)
        if ph:
            src = os.path.join(src_media, ph["file"])
            if os.path.exists(src):
                dst_name = "fotouni_{}.jpg".format(next_id)
                shutil.copyfile(src, os.path.join(IMAGES_OUT, dst_name))
                photo_file = dst_name
                p_lat, p_lon = ph["lat"], ph["lon"]
        row = build_row(r, None, photo_file, p_lat, p_lon, extra_gnss=extra)
        row["id"] = next_id
        next_id += 1
        out_rows.append(row)

    # ---- 4. Independent phone-only contributors: Caleb, Gaby, DJIBRIL ----
    contributor_points = []
    contributors = [
        ("Caleb", "calep.xlsx", "calep"),
        ("Gaby_formidable_za", "Gaby formidable za.xlsx", "Gaby formidable za"),
        ("DJIBRIL_kengni", "DJIBRIL kengni.xlsx", "DJIBRIL kengni"),
    ]
    for folder, fname, media_prefix in contributors:
        path = os.path.join(RAW, folder, fname)
        xl = pd.ExcelFile(path)
        main_sheet = [s for s in xl.sheet_names
                      if s not in ("PHOTOS", "TRACKS", "TRACK_POINTS", "FEATURE_POINTS")][0]
        pts = load_sheet(xl, main_sheet)
        photos = load_photos(xl)
        media_dir = os.path.join(RAW, folder, "Media")
        for r in pts:
            if r["time"] is None:
                continue
            p = dict(r, contributor=folder, photo_src=None, photo_lat=None, photo_lon=None)
            ph = nearest_photo(r["time"], photos, tolerance_s=90)
            if ph:
                src = os.path.join(media_dir, ph["file"])
                if os.path.exists(src):
                    p["photo_src"] = src
                    p["photo_lat"], p["photo_lon"] = ph["lat"], ph["lon"]
                else:
                    unmatched_photos_note.append(ph["file"])
            contributor_points.append(p)

    # ---- 5. Brice: sqlite-only contributor, no xlsx, no photos on disk ----
    con = sqlite3.connect(os.path.join(RAW, "Brice", "database.swm2"))
    cur = con.cursor()
    for remark, lat, lon, t_ms, fix_q, acc_h, bearing in cur.execute(
        "select f.remarks, p.lat, p.lon, p.time, p.fix_quality, p.accuracy_h, p.bearing "
        "from features f join points p on p.fid = f.uuid"):
        t = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc) + timedelta(hours=1)
        contributor_points.append(dict(
            remark_raw=remark, time=t, lat=lat, lon=lon, fix_id=fix_q, h_acc=acc_h,
            bearing=bearing, contributor="Brice", photo_src=None, photo_lat=None, photo_lon=None,
        ))
    con.close()

    # ---- Merge repeated observations of the same physical location -----
    # These four contributors are independent students who walked the
    # village together, all recording at phone-grade accuracy (Fix ID 1 for
    # every single point in all four sources - confirmed by inspecting the
    # raw data) - so the same building was often tapped and described by 2
    # or even 3 of them within seconds of each other. Emitting each as its
    # own row would show the same building 2-3 times on the map; instead,
    # points within 15 m of each other are treated as the same location
    # (verified against the raw data: every genuine repeat observation is
    # under 7 m apart, while distinct nearby buildings are 16 m+ apart -
    # see data/building_datasets.md) and merged into a single row - mean
    # position (averaging independent phone fixes reduces effective error),
    # every contributor's own photo kept, and every contributor's own
    # remark classified and unioned into the row's landuse/products.
    for cluster in cluster_by_location(contributor_points, threshold_m=15.0):
        photo_files = []
        for p in cluster:
            if p["photo_src"]:
                dst_name = "fotouni_{}.jpg".format(next_id)
                shutil.copyfile(p["photo_src"], os.path.join(IMAGES_OUT, dst_name))
                photo_files.append(dst_name)
        row = build_contributor_cluster_row(cluster, photo_files)
        row["id"] = next_id
        next_id += 1
        out_rows.append(row)

    # ---- Drop points explicitly superseded/duplicated per the surveyor's own
    # field remarks ("retake point ..." replaces the immediately preceding
    # point; "(N was already taken so delete)" flags the current point as a
    # duplicate point-number that the surveyor asked to discard). ----
    to_drop = set()
    for idx, row in enumerate(out_rows):
        gnss = row.get("comments_gnss") or ""
        if "retake point" in gnss.lower() and idx > 0:
            to_drop.add(idx - 1)
        if "already taken" in gnss.lower():
            to_drop.add(idx)
    if to_drop:
        print("Dropping {} point(s) superseded/duplicated per surveyor remarks (ids: {})".format(
            len(to_drop), sorted(out_rows[i]["id"] for i in to_drop)))
    for i in to_drop:
        dropped_photo = out_rows[i].get("photo") or ""
        if dropped_photo:
            p = os.path.join(IMAGES_OUT, dropped_photo)
            if os.path.exists(p):
                os.remove(p)
    out_rows = [r for i, r in enumerate(out_rows) if i not in to_drop]
    for new_id, row in enumerate(out_rows, start=1):
        old_photo = row.get("photo") or ""
        if old_photo:
            old_path = os.path.join(IMAGES_OUT, old_photo)
            new_name = "fotouni_{}.jpg".format(new_id)
            new_path = os.path.join(IMAGES_OUT, new_name)
            if old_path != new_path and os.path.exists(old_path):
                os.replace(old_path, new_path)
            row["photo"] = new_name
        row["id"] = new_id

    cols = ["id", "landuse", "products", "rtk_lat", "rtk_lon", "rtk_timestamp",
            "rtk_precision", "mobile_lat", "mobile_lon", "mobile_timestamp",
            "mobile_precision", "mobile_error", "comments_landuse", "comments_gnss",
            "photo", "photo_lat", "photo_lon", "photo_precision", "photo_bearing"]
    df = pd.DataFrame(out_rows)[cols]
    df.to_csv("data/fotouni/fotouni.csv", index=False)
    print("Wrote data/fotouni/fotouni.csv with", len(df), "rows")
    both = df[(df.rtk_lat.notna()) & (df.mobile_lat.notna())]
    rtk_only = df[(df.rtk_lat.notna()) & (df.mobile_lat.isna())]
    phone_only = df[(df.rtk_lat.isna()) & (df.mobile_lat.notna())]
    neither = df[(df.rtk_lat.isna()) & (df.mobile_lat.isna())]
    print("both:", len(both), "rtk_only:", len(rtk_only), "phone_only:", len(phone_only), "neither(BUG):", len(neither))
    n_photos = (df.photo != "").sum()
    print("rows with photo:", n_photos)
    if unmatched_photos_note:
        print("Referenced-but-missing photo files:", unmatched_photos_note)


if __name__ == "__main__":
    main()
