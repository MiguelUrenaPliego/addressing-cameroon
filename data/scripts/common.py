"""
Shared utilities for building the Douala and Fotouni land-use CSV datasets
from raw SW Maps xlsx/sqlite exports.

List-encoding convention: list-valued columns (`landuse`, `products`, `photo`)
are encoded as pipe-delimited strings, e.g. "commercial|office". A point with
no applicable value uses an empty string. This is documented in
data/building_datasets.md.
"""
import math
import re
from datetime import datetime

LIST_SEP = "|"


def encode_list(items):
    items = [i for i in items if i]
    # de-duplicate while preserving order
    seen = []
    for i in items:
        if i not in seen:
            seen.append(i)
    return LIST_SEP.join(seen)


def parse_time(s):
    """Parse SW Maps time strings like '03/20/2026 12:34:14.000 GMT+01:00'."""
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return None
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return None
    # strip trailing timezone name, keep the offset implicit (local time as recorded)
    m = re.match(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}(?:\.\d+)?)", s)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%m/%d/%Y %H:%M:%S.%f" if "." in m.group(1) else "%m/%d/%Y %H:%M:%S")
    return dt


def iso(dt):
    if dt is None:
        return None
    return dt.isoformat()


def sequence_align(rtk_rows, phone_rows, max_pair_distance_m=800.0, gap_penalty=350.0):
    """Order-preserving alignment of two point sequences recorded during
    the same walk, one per device, when the two recordings were NOT made
    at the same time (so timestamps can't be used to match them) but were
    both walked in the same physical order - point 1 in one file
    corresponds to point 1 in the other, point 2 to point 2, etc, except
    where one device has an extra point the other doesn't (a retake,
    a reference-only point, a road point recorded on just one device...).

    This is a classic global sequence alignment (Needleman-Wunsch), using
    haversine distance as the substitution cost (capped: a "match" more
    than ``max_pair_distance_m`` apart is never cheaper than leaving both
    points unmatched, so implausibly-distant pairs are never produced) and
    a fixed ``gap_penalty`` for skipping a point on either side. It relies
    entirely on the two sequences' own order, not on absolute position or
    time, so it's robust to the kind of GPS drift/offset (hundreds of
    meters) two independent walks of the same route can show while still
    visiting stops in the same order.

    Args:
        rtk_rows: RTK points, in their own recording order, each with
            ``lat``/``lon`` keys.
        phone_rows: Phone points, in their own recording order, each with
            ``lat``/``lon`` keys.
        max_pair_distance_m: Points farther apart than this are never
            matched to each other, no matter what the alignment would
            otherwise prefer.
        gap_penalty: Cost of leaving one point unmatched. Should sit
            somewhere between "typical true-match distance" and
            "max_pair_distance_m", so real (if noisy) matches are still
            cheaper than two gaps, but implausible matches are not.

    Returns:
        ``(pairs, unmatched_rtk, unmatched_phone)`` - ``pairs`` is a list
        of ``(rtk_row, phone_row)`` tuples, in order; the other two are
        lists of leftover rows, in their own original order.
    """
    n, m = len(rtk_rows), len(phone_rows)
    no_match_cost = 2 * gap_penalty + 1.0  # always worse than two gaps

    # dp[i][j] = min cost aligning rtk_rows[:i] with phone_rows[:j]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * gap_penalty
    for j in range(1, m + 1):
        dp[0][j] = j * gap_penalty
    for i in range(1, n + 1):
        r = rtk_rows[i - 1]
        for j in range(1, m + 1):
            p = phone_rows[j - 1]
            dist = haversine_m(r["lat"], r["lon"], p["lat"], p["lon"])
            match_cost = dist if (dist is not None and dist <= max_pair_distance_m) else no_match_cost
            dp[i][j] = min(
                dp[i - 1][j - 1] + match_cost,
                dp[i - 1][j] + gap_penalty,
                dp[i][j - 1] + gap_penalty,
            )

    pairs, unmatched_rtk, unmatched_phone = [], [], []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            r, p = rtk_rows[i - 1], phone_rows[j - 1]
            dist = haversine_m(r["lat"], r["lon"], p["lat"], p["lon"])
            match_cost = dist if (dist is not None and dist <= max_pair_distance_m) else no_match_cost
            if abs(dp[i][j] - (dp[i - 1][j - 1] + match_cost)) < 1e-6:
                pairs.append((r, p))
                i -= 1
                j -= 1
                continue
        if j > 0 and (i == 0 or abs(dp[i][j] - (dp[i][j - 1] + gap_penalty)) < 1e-6):
            unmatched_phone.append(phone_rows[j - 1])
            j -= 1
        else:
            unmatched_rtk.append(rtk_rows[i - 1])
            i -= 1
    pairs.reverse()
    unmatched_rtk.reverse()
    unmatched_phone.reverse()
    return pairs, unmatched_rtk, unmatched_phone


def haversine_m(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    try:
        if any(math.isnan(x) for x in (lat1, lon1, lat2, lon2)):
            return None
    except TypeError:
        return None
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Fix ID -> precision convention
# ---------------------------------------------------------------------------
# 0 = no live fix (photo-geotag derived only)
# 1 = autonomous / no correction (phone-grade, ~3-10 m)
# 2 = DGPS (sub-metre to few metres)
# 4 = RTK fixed (~1-5 cm)
# 5 = RTK float (~2 cm - 1 m)
FIX_ID_LABELS = {
    0: "no live GPS fix; position was derived from the photo's geotag",
    1: "an autonomous GPS fix, at phone-grade accuracy",
    2: "a DGPS-corrected fix",
    4: "an RTK fixed solution",
    5: "an RTK float solution",
}


def gnss_lost_comment(fix_id):
    """Comment to append when an RTK-recorded point actually has a low Fix ID."""
    if fix_id in (0, 1, 2):
        return "RTK correction was lost during capture; this point reflects {}.".format(
            FIX_ID_LABELS.get(fix_id, "reduced accuracy")
        )
    return None


# ---------------------------------------------------------------------------
# Text cleanup: French / broken-English -> professional English
# ---------------------------------------------------------------------------
# Exact-phrase replacements applied first (case-insensitive), then generic
# word-level substitutions. This list was built by inspecting the actual
# remarks across both the Douala and Fotouni raw files.
PHRASE_REPLACEMENTS = [
    (r"\bhôtel\b", "hotel"),
    (r"\bresidentiel\b", "residential"),
    (r"\brésidentiel\b", "residential"),
    (r"\bterrain\b", "vacant plot"),
    (r"\babandonner\b", "abandoned"),
    (r"\ben construction\b", "under construction"),
    (r"\bnouveau bâtiment\b", "new building"),
    (r"\bprovanderie\b", "provenderie (animal feed store)"),
    (r"\bprovenderie\b", "provenderie (animal feed store)"),
    # "chief" is the only French-ruler-adjacent word kept in English output
    # (matching "king" -> "chief" below) - the specific "chefferie
    # supérieure" phrase must be translated BEFORE the bare "chefferie"
    # rule, or the bare rule consumes "chefferie" first and this one never
    # matches.
    # Cameroon's customary chieftaincy hierarchy has three official
    # administrative degrees; "chefferie supérieure" specifically denotes
    # the 2nd-degree chiefdom, while a plain "chefferie" (no qualifier) is
    # the base, most common 3rd-degree (village-level) chiefdom - not just
    # a vague "senior" vs. "regular" distinction.
    (r"\bchefferie sup[ée]rieure?\b", "second-degree chief's palace"),
    (r"\bchiefferie sup[ée]rieure?\b", "second-degree chief's palace"),
    (r"\bchefferie\b", "third-degree chief's palace"),
    (r"\bchiefferie\b", "third-degree chief's palace"),
    (r"\bbriqueterie\b", "brickworks"),
    (r"\bmenuiserie\b", "carpentry / woodworking shop"),
    (r"\bcordonnier\b", "shoe repair / cobbler"),
    (r"\bplomberie\b", "plumbing shop"),
    (r"\bplombing\b", "plumbing shop"),
    (r"\bquincaillerie\b", "hardware store"),
    (r"\bquincailkerie\b", "hardware store"),
    (r"\bmatériel\b", "materials"),
    (r"\bmatériaux\b", "materials"),
    (r"\bélectricité\b", "electricity"),
    (r"\bélectricity\b", "electricity"),
    (r"\bkinésithérapie\b", "physiotherapy"),
    (r"\bkinesitherapie\b", "physiotherapy"),
    (r"\bcentre médical\b", "medical centre"),
    (r"\bcentre imagerie médicale\b", "medical imaging centre"),
    (r"\bcentre de sante\b", "health centre"),
    (r"\bcentre du sante\b", "health centre"),
    (r"\binshurace\b", "insurance"),
    (r"\bassurance\b", "insurance"),
    (r"\bfoyer culturel\b", "cultural centre"),
    (r"\bpetit marche\b", "small market"),
    (r"\bmarche\b", "market"),
    # multi-word phrases translated (and reordered into English word order)
    # BEFORE the single-word rules below, so e.g. "lycée technique" becomes
    # "technical high school" rather than "high school technique"
    (r"\blycee technique\b", "technical high school"),
    (r"\blycée technique\b", "technical high school"),
    (r"\becole maternelle\b", "nursery school"),
    (r"\bécole maternelle\b", "nursery school"),
    (r"\bcentre de servis\b", "service centre"),
    (r"\blycee\b", "high school"),
    (r"\blycée\b", "high school"),
    (r"\becole\b", "school"),
    (r"\bcodefo\b", "CODEFO (local organisation)"),
    (r"\bgranero\b", "grain store"),
    (r"\bferretería\b", "hardware store"),
    (r"\bécole\b", "school"),
    (r"\bmarché\b", "market"),
    (r"\bmaison d'habitation\b", "residential house"),
    (r"\bplace des fêtes\b", "festival ground"),
    (r"\bplase des fêtes\b", "festival ground"),
    (r"\bking house\b", "chief's residence"),
    (r"\bking's house\b", "chief's residence"),
    (r"\bking palace\b", "chief's palace"),
    (r"\bof king\b", "of the chief"),
    (r"\bthe king\b", "the chief"),
    (r"\bking\b", "chief"),
    (r"\bwatertower\b", "water tower"),
    (r"\bmotocycle\b", "motorcycle"),
    # field-survey workflow notes, reworded to formal English while
    # preserving their exact original meaning
    (r"\bunder construction not in google\b", "under construction; not yet shown on Google Maps"),
    (r"\bnot in google under construction\b", "under construction; not yet shown on Google Maps"),
    (r"\bnew not in google\b", "recently built; not yet shown on Google Maps"),
    (r"\bnot in google maybe\b", "possibly not shown on Google Maps"),
    (r"\bthis building in google does not exist\b", "this building does not appear on Google Maps"),
    (r"\bnot in google\b", "not shown on Google Maps"),
    (r"\bsame id\b", "duplicate point number"),
    (r"\bignore before on this layer\b",
     "the surveyor requested that points preceding this one on this layer be disregarded"),
    (r"\bretake point before strange in this dataset\b",
     "this point is a retake; the preceding point showed anomalous data and was removed from the dataset"),
    (r"^photograph$", "recorded to capture a photograph only"),
    (r"\bman did not want\b", "the property owner declined to be surveyed"),
    (r"\baccess through (\d+)\b", r"accessed via point \1"),
]

WORD_FIXES = {
    "sel": "sale",  # aluminum sel -> aluminum sale
    "shool": "school",
    "cundtruction": "construction",
    "constrction": "construction",
    "weldering": "welding",
    "vine": "wine",
    "clothesand": "clothes and",
    "sjop": "shop",
    "stire": "store",
    "shof": "shop",
    "apliance": "appliance",
    "resident": "residential",
    "cosmetand": "cosmetics and",
    "cosmetics": "cosmetics",
    "provanderie": "provenderie",
    "provenderi": "provenderie",
    "roqd": "road",
    # French connector/adjective words left over after the phrase-level
    # translations above, so a remark reads fully in English rather than a
    # French/English mix (e.g. "high school technique de fotouni" ->
    # "technical high school of fotouni")
    "de": "of",
    "du": "of the",
    "des": "of the",
    "numero": "number",
    "numéro": "number",
    "paysans": "farmers'",
    "servis": "services",
}


def strip_leading_number(raw):
    """Strip the surveyor's leading point-number prefix from a remark, e.g.
    '55 health centre' -> 'health centre', '82R' -> 'R'. The number itself is
    only a cross-reference used to line up rtk/phone pairs and carries no
    landuse information."""
    return re.sub(r"^\s*\d+\s*", "", raw)


def clean_text(text):
    """Translate/clean a raw French/broken-English remark into professional
    English while preserving the original meaning. Returns (clean, raw)."""
    if text is None:
        return "", ""
    raw = str(text).strip()
    if not raw or raw.lower() == "nan":
        return "", ""
    t = strip_leading_number(raw)
    for pat, repl in PHRASE_REPLACEMENTS:
        t = re.sub(pat, repl, t, flags=re.IGNORECASE)
    words = t.split()
    fixed = []
    for w in words:
        key = w.strip(".,;:").lower()
        fixed.append(WORD_FIXES.get(key, w))
    t = " ".join(fixed)
    t = re.sub(r"\s+", " ", t).strip()
    # capitalize the first letter of the string and of every sentence
    # after a ". " (clean_text can join two source phrases with a period,
    # e.g. "same id" + "old brother of..." -> "Duplicate point number. old
    # brother..." - both halves should read as proper sentences)
    if t:
        t = t[0].upper() + t[1:]
        t = re.sub(r"(\. )([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    return t, raw


# ---------------------------------------------------------------------------
# Landuse / products controlled vocabulary and classification rules
# ---------------------------------------------------------------------------
LANDUSE_VALUES = [
    "residential", "commercial", "office", "industry", "public_service", "road",
]

PRODUCTS_VALUES = [
    "clothing", "electronics", "mobile_phones", "beauty_services",
    "construction_materials", "plumbing_services", "shoe_repair_services",
    "optics", "jewelry", "food", "drinks", "hotel", "restaurant", "bar",
    "pub", "nightclub", "banking_and_finance", "healthcare", "education",
    "religious_services", "automotive", "general_retail",
    "furniture_and_home_goods", "dry_cleaning_services", "gas_and_fuel",
    "agriculture", "professional_services", "footpath", "drive",
    "kitchen", "storage_and_warehousing", "brick_production", "metalwork",
    "carpentry_and_woodwork",
]

# Ordered keyword -> (landuse, products) rules, built by manually reading
# every distinct remark string in both raw datasets (see
# data/building_datasets.md section 3 for the methodology) rather than by
# guessing generic patterns - each key below corresponds to actual observed
# vocabulary. First match wins for landuse per rule; products can
# accumulate across several matching rules. Keys are matched as substrings
# on the lower-cased, leading-number-stripped remark, except a key starting
# with "^" which must match the ENTIRE cleaned remark exactly.
KEYWORD_RULES = [
    # literal road/path references (Douala's "Ro"/"Ro2"/"Ro3" opening
    # points, Fotouni's "road"/"path"/"roqd" (typo, word-fixed to "road")
    # - a handful of true street/track points, not to be confused with the
    # "R"/"Rs" residential shorthand below)
    (["^ro$", "^ro2$", "^ro3$"], "road", ["drive"]),
    (["road"], "road", ["drive"]),
    (["path"], "road", ["footpath"]),
    # "R"/"Rs" residential shorthand: one Douala surveyor's own abbreviation
    # for "residential"/"residential units", confirmed by cross-checking
    # against another Douala file from a different surveyor that spells
    # "residential" out in full and never uses bare "R" - manually verified
    # across ~300 occurrences, always appearing where a plain house is
    # expected (not a road). Includes combinations like "R+Rs", "Rs+shops"
    # (a shop with residential units above/behind it), "R+barber shop"
    # (mixed-use), "auto+R", "R+construction".
    (["^r$", "^rs$", "^r+$"], "residential", []),
    (["r+rs", "rs+", "+rs", "r+", "+r", "r +", "+ r"], "residential", []),
    (["^rest$"], "commercial", ["restaurant", "food"]),
    (["lounge", "cave"], "commercial", ["bar", "drinks"]),
    # public service - health
    (["health centre", "health center", "centre sanitaire", "medical centre",
      "medical center", "medical imaging", "centre imagery", "clinic",
      "pharmacy", "pharmacies", "dental", "centre du sante", "sanitaire",
      "fitness", "centre medical", "hospital", "physiotherapy"],
     "public_service", ["healthcare"]),
    # public service - education
    (["school", "college", "kindergarten", "kind garten", "vocational center",
      "vocational centre", "voc center"], "public_service", ["education"]),
    # public service - traditional authority / community / civic (no
    # dedicated category exists for customary institutions; religious_services
    # is used as the closest available tag, documented in building_datasets.md)
    (["chieftaincy", "chefferie", "chiefferie", "sub chief",
      "chief's residence", "chief's palace", "royal building",
      "king house", "traditional building", "traditional house",
      "ceremony house", "festival ground", "place des fêtes",
      "place des fetes", "community place", "foyer", "cultural centre",
      "secretariat development", "president of development",
      "president of festivities"],
     "public_service", ["religious_services"]),
    (["church", "temple", "mosque", "evangelical"], "public_service", ["religious_services"]),
    (["fountain", "water tower", "watertower"], "public_service", []),
    # offices / banking
    (["bank", "ecobank", "panafrican bank"], "commercial", ["banking_and_finance"]),
    (["insurance", "agc assurance", "inshurace"], "office", ["banking_and_finance"]),
    (["office", "agence de voyage", "travel agency", "abis agency",
      "abis egency"], "office", ["professional_services"]),
    (["radio", "codefo"], "office", ["professional_services"]),
    (["photocopy"], "office", ["professional_services"]),
    # construction trades - materials/shops vs. specific production/services
    # split out by what TYPE of industry/trade it actually is, not lumped
    # under one generic "construction_materials" tag
    (["construction material", "construction matériel", "construction shop",
      "materials shop", "material shop", "quincaillerie", "quincailkerie",
      "hardware store", "cement", "ferretería", "ferreteria"],
     "commercial", ["construction_materials"]),
    # brickworks/"briqueterie" is a brick-production site, an industrial
    # use, not a retail shop selling materials
    (["brickworks", "briqueterie"], "industry", ["brick_production"]),
    (["plomberie", "plombing", "pkombing", "plumber", "plumbing", "plomb"],
     "commercial", ["plumbing_services"]),
    (["welding", "weldering", "aluminum work", "aluminum sale", "aluminum sel"],
     "industry", ["metalwork"]),
    (["carpentry", "menuiserie", "wood work"], "industry", ["carpentry_and_woodwork"]),
    # bare "workshop" with no further qualifier reads as a fabrication/repair
    # workshop (an industrial use) rather than retail - it is NOT one of the
    # "general_retail" catch-all's "shop"/"shops" (checked with a word
    # boundary specifically so it never matches inside "workshop")
    (["workshop"], "industry", []),
    (["cordonnier", "shoe repair"], "commercial", ["shoe_repair_services"]),
    # a kitchen recorded separately from its residential building is still
    # residential, not an industrial/commercial food-production use
    (["kitchen", "cuisine"], "residential", ["kitchen"]),
    # automotive
    (["auto school", "driving school", "auto repair", "vehicle repair",
      "vehicle repai", "vehicle part", "moto repair", "moto part",
      "motorcycle repair", "motocycle repair", "wheel", "tyre", "tire",
      "automobile", "auto+", "+auto", "saviour automobile", "automile",
      "ac repair", "car mechanic", "mechanic", "garage"],
     "commercial", ["automotive"]),
    # food & beverage / hospitality - specific venue type first
    (["restaurant", "cafe"], "commercial", ["restaurant", "food"]),
    (["night club", "nightclub"], "commercial", ["nightclub", "drinks"]),
    (["pub"], "commercial", ["pub", "drinks"]),
    # \b...\b: "bar" as a plain substring would also match inside "barber"
    (["\\bbar\\b", "snack"], "commercial", ["bar", "drinks"]),
    (["food shop", "food market", "storage food", "grain store", "granero",
      "basic need"], "commercial", ["food"]),
    (["wine store", "wine +", "wine+", "+ wine", "vine +", "vine+"],
     "commercial", ["drinks"]),
    (["hotel", "motel", "hôtel"], "commercial", ["hotel"]),
    # beauty
    (["beauty", "hair", "barber", "cosmetic", "beat shop"],
     "commercial", ["beauty_services"]),
    # clothing (checked before the generic "shop"/general_retail catch-all)
    (["cloth", "clothing", "tailor", "tieds shop", "tier shop",
      "love shop", "m leuko", "mleuko"], "commercial", ["clothing"]),
    # boutique alone is a generic shop, not necessarily clothing
    (["boutique"], "commercial", ["general_retail"]),
    # electronics / phones
    (["electronic", "electric shop", "electrical material", "electric material",
      "electrical appliance", "electrical apliance", "electric material",
      "chint electric", "chint électricity", "elecam", "tv shop", "electric"],
     "commercial", ["electronics"]),
    (["communication", "phone repair", "phones", "phone repairs"],
     "commercial", ["mobile_phones"]),
    (["diamond shop"], "commercial", ["jewelry"]),
    (["eye optics", "optical"], "commercial", ["optics"]),
    # furniture / home goods
    (["decor", "furniture", "luminaire", "rideau"],
     "commercial", ["furniture_and_home_goods"]),
    (["gaz"], "commercial", ["gas_and_fuel"]),
    (["pressing", "washing cloth"], "commercial", ["dry_cleaning_services"]),
    # agriculture
    (["provenderie", "provanderie", "provenderi", "agriculture cooperative",
      "agriculture shop", "farm", "plants du bled", "plats du bled",
      "animal food", "animal feed"], "commercial", ["agriculture"]),
    (["field", "farmland", "plantation"], "residential", ["agriculture"]),
    # storage / warehousing - an industrial use, not retail
    (["storage"], "industry", ["storage_and_warehousing"]),
    # general commerce fallback - generic shop/market words carrying no
    # more specific product signal. "shop"/"shops" use \b...\b since, as a
    # plain substring, "shop" would also match inside "workshop"
    # (a fabrication workshop, handled above as industry, not retail).
    (["multiservice", "multipurpose building", "multi construction",
      "multiple commerce", "multiple sale", "multiple sel", "multiple serv",
      "shopping", "\\bshops?\\b", "mini market", "market", "store",
      "music center", "music centre", "molticommerce", "multicommerce",
      "nice shop"], "commercial", ["general_retail"]),
    # incomplete / abandoned / old structures -> residential (vacant plot
    # or ordinary house, not a commercial use)
    (["abandon", "incomplete buil", "uncompleted resid", "destroyed",
      "in ruins", "old building", "new building", "nouveau bâtiment",
      "traditional house under construction"], "residential", []),
    # generic construction fallback (checked after specific construction
    # rules and after plumbing/welding, so those keep their own tag)
    (["construction"], "commercial", ["construction_materials"]),
    (["service house", "residential house", "maison d'habitation"],
     "residential", []),
    # residential
    (["residential", "résidentiel", "petit terrain", "vacant plot",
      "terrain"], "residential", []),
]

# Remarks that describe the *position* of a point relative to a nearby
# feature (not the land use of the point itself), e.g. "main entrance",
# "access through 89". These are recorded in comments_gnss (position-related
# notes) rather than comments_landuse, and do not count as an unmapped
# landuse remark.
POSITION_NOTE_PATTERNS = [
    r"\bmain entrance\b", r"\bentrance\b", r"\bfence entrance\b",
    r"\baccessed via point\b", r"^\s*entrance\s*$",
]

# Field-survey workflow notes (retakes, duplicate point numbers, dataset
# bookkeeping) that are not land-use information at all. Recorded verbatim
# (cleaned) in comments_gnss for traceability.
SURVEY_NOTE_PATTERNS = [
    r"this point is a retake", r"duplicate point number",
    r"surveyor requested that points preceding", r"already taken",
    r"google maps", r"does not appear on google maps",
    r"^recorded to capture a photograph only$",
    r"declined to be surveyed",
]


def is_position_note(clean_remark):
    low = clean_remark.lower()
    return any(re.search(p, low) for p in POSITION_NOTE_PATTERNS)


def is_survey_note(clean_remark):
    low = clean_remark.lower()
    return any(re.search(p, low) for p in SURVEY_NOTE_PATTERNS)


def classify(clean_remark):
    """Return (landuse_list, products_list) from a cleaned remark string."""
    stripped = clean_remark.lower().strip()
    low = " " + stripped + " "
    landuse = []
    products = []
    for keys, lu, prods in KEYWORD_RULES:
        for k in keys:
            if k.startswith("^"):
                pat = k[1:].rstrip("$")
                matched = stripped == pat
            elif k.startswith(r"\b"):
                # A handful of short keys ("bar", "shop"...) are also
                # substrings of unrelated words ("barber", "workshop") -
                # these are written as an explicit \b...\b regex rather
                # than a plain substring, unlike most keys here, which rely
                # on substring/prefix matching on purpose (e.g. "cloth"
                # matching "clothes"/"clothing").
                matched = re.search(k, low) is not None
            else:
                matched = k in low
            if matched:
                landuse.append(lu)
                products.extend(prods)
                break
    defaulted = not landuse
    if defaulted:
        landuse = ["residential"]
    return landuse, products, defaulted


def is_road_remark(remark_raw) -> bool:
    """Whether a raw remark (before pairing) classifies as a road/path
    point on its own - used to pull these out of an RTK/phone sequence
    BEFORE alignment, since a one-sided road/reference marker (recorded by
    only one of the two devices) would otherwise consume a slot in the
    sequence and throw off the alignment of the real points around it."""
    clean, _raw = clean_text(remark_raw)
    if not clean:
        return False
    landuse, _products, _defaulted = classify(clean)
    return "road" in landuse


def is_implausible_pair(rtk_row, phone_row, factor=5.0):
    """Whether an RTK/phone pair proposed by an alignment step is too far
    apart to plausibly be the same physical point, given the phone's own
    reported precision - a strong sign of a wrong match (e.g. an alignment
    or leading-number coincidence) rather than genuine phone GNSS drift.

    A match is rejected if the RTK/phone distance exceeds ``factor`` times
    the phone's own ``Horizontal Accuracy`` (its self-reported 1-sigma
    precision) - a real reading essentially never drifts that far past its
    own stated uncertainty, whereas a mismatched pair (two unrelated
    physical points) routinely does.

    Args:
        rtk_row: The RTK-side row (dict with ``lat``/``lon``), or None.
        phone_row: The phone-side row (dict with ``lat``/``lon``/``h_acc``),
            or None.
        factor: How many multiples of the phone's own precision the
            distance may reach before the pair is rejected.

    Returns:
        True if both rows are given and the distance between them exceeds
        ``factor`` times the phone's precision; False otherwise (including
        when either row is missing, or the phone precision is missing/zero).
    """
    if rtk_row is None or phone_row is None:
        return False
    precision = phone_row.get("h_acc")
    if precision is None or precision <= 0:
        return False
    distance = haversine_m(rtk_row["lat"], rtk_row["lon"], phone_row["lat"], phone_row["lon"])
    if distance is None:
        return False
    return distance > factor * precision


def reject_implausible_pairs(pairs, unmatched_rtk, unmatched_phone, factor=5.0):
    """Split out any pair whose RTK/phone distance is implausible given the
    phone's own precision (see ``is_implausible_pair``) - an alignment step
    (chronological or sequence-based) can still occasionally propose a
    pairing that is simply wrong (e.g. a coincidental nearest-in-order match
    between two actually-unrelated points); this is a final sanity check
    applied after alignment, independent of how the pairs were produced.

    Args:
        pairs: List of (rtk_row, phone_row) tuples from an alignment step.
        unmatched_rtk: That step's own list of leftover RTK-only rows.
        unmatched_phone: That step's own list of leftover phone-only rows.
        factor: Passed through to ``is_implausible_pair``.

    Returns:
        ``(good_pairs, unmatched_rtk, unmatched_phone)`` - rejected pairs'
        rows are appended to the two unmatched lists (order otherwise
        preserved), so no data is lost, only the (wrong) pairing.
    """
    good_pairs = []
    unmatched_rtk = list(unmatched_rtk)
    unmatched_phone = list(unmatched_phone)
    for rtk_row, phone_row in pairs:
        if is_implausible_pair(rtk_row, phone_row, factor=factor):
            unmatched_rtk.append(rtk_row)
            unmatched_phone.append(phone_row)
        else:
            good_pairs.append((rtk_row, phone_row))
    return good_pairs, unmatched_rtk, unmatched_phone
