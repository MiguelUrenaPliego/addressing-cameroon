"""POI category taxonomy shared by both places.

Each survey row can belong to zero, one, or several of the 11 categories
required by the spec. Membership is derived from the pipe-separated
`products` tag list (populated for commercial/office/industry rows) and,
for `public_service` rows (whose `products` column is empty), from a
free-text `comments_landuse` field, matched case-insensitively with regexes.

`landuse == "road"` rows and residential-only rows (landuse == "residential",
no other tag) are never POIs.
"""
import re

# category_key -> (display name, emoji, {product tags}, comments regex or None)
CATEGORIES = {
    "fountain": (
        "Fountain", "💧", set(),
        re.compile(r"fountain|water\s*tower|\bwell\b|\bpump\b", re.I),
    ),
    "healthcare": (
        "Healthcare", "🏥", {"healthcare"},
        re.compile(r"hospital|health\s*cent|pharmac|medical\s*cent|physiotherapy|dental|centre\s*imagery|centre\s*medical|centre\s*sanitaire", re.I),
    ),
    "school": (
        "School", "🏫", {"education"},
        re.compile(r"school|kind.?\s*garten|kindergarten|college|nursery|vocational|voc\s*center|technical\s*(school|high\s*school)|auto\s*school|driving\s*school", re.I),
    ),
    "food": (
        "Food", "🍽️", {"restaurant", "food"},
        None,
    ),
    "clothes": (
        "Clothes", "👕", {"clothing"},
        None,
    ),
    "construction": (
        "Construction", "🧱", {"construction_materials"},
        None,
    ),
    "electronics": (
        "Electronics", "🔌", {"electronics", "mobile_phones"},
        None,
    ),
    "car_repairs": (
        "Car repairs/parts", "🚗", {"automotive"},
        None,
    ),
    "animal_food": (
        "Animal food", "🌾", {"agriculture"},
        re.compile(r"animal\s*feed|livestock\s*feed|élevage|elevage", re.I),
    ),
    "bar_pub_club": (
        "Bar/Pub/Club", "🍺", {"bar", "drinks", "nightclub"},
        None,
    ),
    "public_building": (
        "Public building/space", "🏛️", {"religious_services"},
        re.compile(
            r"royal|chief|palace|traditional\s*(house|authority|building)|cultural\s*centre|"
            r"community\s*place|\bfoyer\b|festival\s*ground|ceremony\s*house|\bchurch\b|"
            r"secretariat|house\s*of\s*president",
            re.I,
        ),
    ),
}

CATEGORY_ORDER = list(CATEGORIES.keys())


def _humanize_products(products: str) -> str:
    tags = [t.strip() for t in (products or "").split("|") if t.strip()]
    return ", ".join(t.replace("_", " ").title() for t in tags)


_COMMENT_TYPO_FIXES = [
    (re.compile(r"kind\.?\s*garten", re.I), "Kindergarten"),
    (re.compile(r"\bvoc\s*center\b", re.I), "Vocational center"),
    (re.compile(r"\bvoc\b", re.I), "Vocational"),
]


def _humanize_comment(comment: str) -> str:
    """Survey free-text notes carry landuse shorthand noise (e.g. the leading
    'R+'/'Rs' residential-mix markers in 'R+driving school') and typos
    ('Kind garten', 'Voc center') that shouldn't be shown to map viewers."""
    c = (comment or "").strip()
    if not c:
        return ""
    c = re.sub(r"^(r\s*\+\s*|rs\s+|r\s+)", "", c, flags=re.I)
    c = re.sub(r"\s*\+\s*", ", ", c)
    for pattern, repl in _COMMENT_TYPO_FIXES:
        c = pattern.sub(repl, c)
    c = c.strip(" ,")
    return c[0].upper() + c[1:] if c else ""


# Specific facility descriptions read from the free-text survey notes beat a
# generic product-tag dump (e.g. a driving school is tagged
# education|automotive because it teaches driving *and* uses cars, but a map
# viewer wants "Driving school", not "Education, Automotive"). Checked in
# order; first match wins.
#
# Public-service rows in the survey are tagged products="religious_services"
# as a catch-all for essentially every traditional-authority/civic building
# (chief's palaces, cultural centres, festival grounds, secretariats, "house
# of president of ..." — see comments_landuse) — only a literal church is
# actually religious. Left unhandled, poi_label would show "Religious
# Services" for a royal palace. The rules below read the real facility type
# from the comment and take priority over that misleading tag.
_LABEL_RULES = [
    (re.compile(r"driving\s*school|auto\s*school", re.I), "Driving school"),
    (re.compile(r"kind\.?\s*garten|kindergarten", re.I), "Kindergarten"),
    (re.compile(r"nursery", re.I), "Daycare / nursery school"),
    (re.compile(r"technical\s*(high\s*)?school|technical\s*secondary", re.I), "Technical secondary school"),
    (re.compile(r"high\s*school|secondary\s*school|\bcollege\b", re.I), "Secondary school"),
    (re.compile(r"primary\s*school", re.I), "Primary school"),
    (re.compile(r"vocational|\bvoc\s*center\b", re.I), "Vocational training center"),
    (re.compile(r"\bschool\b", re.I), "School (level unspecified)"),
    (re.compile(r"\bchurch\b|\bmosque\b|\btemple\b|evangelical", re.I), "Church"),
    (re.compile(r"royal|chief|palace", re.I), "Traditional authority palace"),
    (re.compile(r"cultural\s*centre", re.I), "Cultural center"),
    (re.compile(r"community\s*place|\bfoyer\b", re.I), "Community hall"),
    (re.compile(r"festival\s*ground", re.I), "Festival ground"),
    (re.compile(r"ceremony\s*house", re.I), "Ceremony house"),
    (re.compile(r"secretariat", re.I), "Administrative office"),
    (re.compile(r"house\s*of\s*president", re.I), "Community leadership house"),
    (re.compile(r"traditional\s*(house|building)", re.I), "Traditional building"),
]


def _match_label_rules(comment: str) -> str:
    c = comment or ""
    for pattern, label in _LABEL_RULES:
        if pattern.search(c):
            return label
    return ""


def poi_label(products: str, comments_landuse: str) -> str:
    """Human-readable POI description for map tooltips/popups.

    A specific facility description parsed from `comments_landuse` (e.g.
    "Driving school", "Primary school") takes priority over the structured
    `products` tags, since the tags alone can be misleadingly generic or
    even multi-purpose (education|automotive for a driving school). Falls
    back to the product tags, then to a cleaned raw comment.
    """
    specific = _match_label_rules(comments_landuse)
    if specific:
        return specific
    label = _humanize_products(products)
    if label:
        return label
    return _humanize_comment(comments_landuse) or "Unlabeled point"


def categorize_row(landuse: str, products: str, comments_landuse: str) -> set[str]:
    """Return the set of category keys a survey row belongs to."""
    landuse = (landuse or "").strip().lower()
    if landuse == "road" or landuse == "":
        return set()
    if landuse == "residential":
        # residential-only rows are never POIs
        return set()

    tags = {p.strip().lower() for p in (products or "").split("|") if p.strip()}
    comment = comments_landuse or ""

    cats = set()
    for key, (_name, _emoji, product_tags, comment_re) in CATEGORIES.items():
        if product_tags & tags:
            cats.add(key)
            continue
        if comment_re is not None and comment_re.search(comment):
            cats.add(key)
    return cats
