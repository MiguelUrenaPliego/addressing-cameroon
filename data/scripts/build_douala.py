"""
Build data/douala/douala.csv from the raw SW Maps xlsx exports under
data/douala/raw/block_1 and block_2.

See data/building_datasets.md for full methodology.
"""
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from common import (
    parse_time, iso, haversine_m, clean_text, classify, encode_list,
    gnss_lost_comment, is_position_note, is_survey_note, is_road_remark,
    reject_implausible_pairs,
)

RAW = "data/douala/raw"


def load_main_sheet(path):
    xl = pd.ExcelFile(path)
    main_sheets = [s for s in xl.sheet_names
                   if s not in ("PHOTOS", "TRACKS", "TRACK_POINTS", "FEATURE_POINTS")]
    df = xl.parse(main_sheets[0])
    rows = []
    for _, r in df.iterrows():
        remark = r.get("Remarks")
        t = parse_time(r.get("Time"))
        rows.append(dict(
            remark_raw=remark,
            time=t,
            lat=r.get("Latitude"),
            lon=r.get("Longitude"),
            fix_id=r.get("Fix ID"),
            h_acc=r.get("Horizontal Accuracy"),
            bearing=r.get("Bearing"),
        ))
    rows = [r for r in rows if r["time"] is not None]
    rows.sort(key=lambda r: r["time"])
    return rows


def split_road_rows(rows):
    """Pull out points whose own remark reads as a road/reference marker
    (e.g. Douala's "Ro"/"Ro2"/"Ro3") before alignment - a road point
    recorded by only one of the two devices would otherwise consume a slot
    in the sequence and throw off the pairing of the real points around
    it. Returns (non_road_rows, road_rows), both in original order."""
    non_road, road = [], []
    for r in rows:
        (road if is_road_remark(r.get("remark_raw")) else non_road).append(r)
    return non_road, road


def align(rtk_rows, phone_rows, tolerance_s=150):
    """Two-pointer chronological alignment of two near-monotonic sequences
    recorded during the same walk by two devices."""
    i = j = 0
    pairs = []
    unmatched_rtk = []
    unmatched_phone = []
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


def build_row(rtk, phone):
    comments_gnss = []
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
    # category captures (a business name, a note like "same building as
    # 47") that a reader shouldn't lose just because the point was
    # successfully classified.
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
    )


def main():
    walks = []

    # Walk A: "Ange" walk. RTK recorded across two file exports
    # (Ange project 1_042353.xlsx for points ~1-54, miguel_rtk.xlsx for ~55-127);
    # phone recorded continuously in Ange Project 2.xlsx.
    ange1 = load_main_sheet(os.path.join(RAW, "block_1", "Ange project 1_042353.xlsx"))
    miguel_rtk = load_main_sheet(os.path.join(RAW, "block_1", "miguel_rtk.xlsx"))
    rtk_ange = sorted(ange1 + miguel_rtk, key=lambda r: r["time"])
    phone_ange = load_main_sheet(os.path.join(RAW, "block_1", "Ange Project 2.xlsx"))
    walks.append(("ange_walk", rtk_ange, phone_ange))

    # Walk B: Bonamoussadi residential street (block 1).
    # bonamoussadi_041222.xlsx = RTK capture (majority Fix 4/5).
    # bonamoussadi.xlsx = phone capture (majority Fix 1/2), same 189 points.
    rtk_b1 = load_main_sheet(os.path.join(RAW, "block_1", "bonamoussadi_041222.xlsx"))
    phone_b1 = load_main_sheet(os.path.join(RAW, "block_1", "bonamoussadi.xlsx"))
    walks.append(("bonamoussadi_block1", rtk_b1, phone_b1))

    # Walk C: Bonamoussadi day-2 commercial strip (block 2), RTK-only.
    # Two near-identical exports exist; "_033219" retains genuine RTK/DGPS
    # fixes (Fix ID 2/4/5 for ~72% of points) while "bonamoussadi day 2.xlsx"
    # degraded to pure autonomous fixes (Fix ID 1/2 only, no 4/5) -- the
    # latter is treated as a lower-quality duplicate export and dropped.
    rtk_c = load_main_sheet(os.path.join(RAW, "block_2", "bonamoussadi day 2_033219.xlsx"))
    walks.append(("bonamoussadi_day2", rtk_c, []))

    # Walk D: Merveille/mere walk (block 2). RTK + matched phone file.
    rtk_d = load_main_sheet(os.path.join(RAW, "block_2", "merveille_miguel_rtk.xlsx"))
    phone_d = load_main_sheet(os.path.join(RAW, "block_2", "mere_miguel_phone.xlsx"))
    walks.append(("merveille_mere", rtk_d, phone_d))

    out_rows = []
    next_id = 1
    for name, rtk_rows, phone_rows in walks:
        rtk_non_road, rtk_road = split_road_rows(rtk_rows)
        phone_non_road, phone_road = split_road_rows(phone_rows)
        pairs, unmatched_rtk, unmatched_phone = align(rtk_non_road, phone_non_road)
        pairs, unmatched_rtk, unmatched_phone = reject_implausible_pairs(pairs, unmatched_rtk, unmatched_phone)
        unmatched_rtk = unmatched_rtk + rtk_road
        unmatched_phone = unmatched_phone + phone_road
        for rtk, phone in pairs:
            row = build_row(rtk, phone)
            row["id"] = next_id
            next_id += 1
            out_rows.append(row)
        for rtk in unmatched_rtk:
            row = build_row(rtk, None)
            row["id"] = next_id
            next_id += 1
            out_rows.append(row)
        for phone in unmatched_phone:
            row = build_row(None, phone)
            row["id"] = next_id
            next_id += 1
            out_rows.append(row)

    cols = ["id", "landuse", "products", "rtk_lat", "rtk_lon", "rtk_timestamp",
            "rtk_precision", "mobile_lat", "mobile_lon", "mobile_timestamp",
            "mobile_precision", "mobile_error", "comments_landuse", "comments_gnss"]
    df = pd.DataFrame(out_rows)[cols]
    os.makedirs("data/douala", exist_ok=True)
    df.to_csv("data/douala/douala.csv", index=False)
    print("Wrote data/douala/douala.csv with", len(df), "rows")
    both = df[(df.rtk_lat.notna()) & (df.mobile_lat.notna())]
    rtk_only = df[(df.rtk_lat.notna()) & (df.mobile_lat.isna())]
    phone_only = df[(df.rtk_lat.isna()) & (df.mobile_lat.notna())]
    neither = df[(df.rtk_lat.isna()) & (df.mobile_lat.isna())]
    print("both:", len(both), "rtk_only:", len(rtk_only), "phone_only:", len(phone_only), "neither(BUG):", len(neither))


if __name__ == "__main__":
    main()
