"""Parsing for genealogy-evidence-forge.

Turns free-form research notes into structured claims:
  * dates  -> year / month / day / precision / qualifier / ambiguity
  * places -> country / state / county / city
  * records -> one person claim each, with events, relations and sources

No network calls. Pure standard library.
"""

import re

from gh_sources import (
    AU_STATES,
    CANADA_PROVINCES,
    COUNTRIES,
    SOURCE_TYPES,
    STATE_ADMISSION_YEAR,
    UK_COUNTIES,
    US_STATES,
)

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]

CURRENT_YEAR = 2026

# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_RE_DMY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_RE_MDY = re.compile(
    r"\b([A-Za-z]{3,9})\.?,?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_RE_NUMERIC = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_RE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_RE_YEAR = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")
_RE_BETWEEN = re.compile(r"\bbetween\s+(1[5-9]\d{2}|20[0-2]\d)\s+and\s+(1[5-9]\d{2}|20[0-2]\d)", re.I)
_RE_BEFORE = re.compile(r"\b(?:before|prior to|before the year)\s+(1[5-9]\d{2}|20[0-2]\d)", re.I)
_RE_AFTER = re.compile(r"\b(?:after|from|since)\s+(1[5-9]\d{2}|20[0-2]\d)", re.I)
_RE_CIRCA = re.compile(r"\b(?:c\.|ca\.|circa|about|abt\.?|approx\.?|around)\s*(\d{4})", re.I)
_RE_AGE = re.compile(r"\b(?:age|aged|age of)\s+(\d{1,3})\b", re.I)
_RE_DECADE = re.compile(r"\b(1[5-9]\d0|20[0-2]\d0)s\b")


def _month(name):
    return MONTHS.get(name.lower().rstrip("."))


def parse_date(text):
    """Parse a date-ish string into a structured dict.

    Returns a dict with keys: raw, year, month, day, precision, qualifier,
    circa, ambiguous, year_start, year_end, note.
    """
    raw = (text or "").strip()
    out = {
        "raw": raw,
        "year": None,
        "month": None,
        "day": None,
        "precision": "unknown",
        "qualifier": "none",
        "circa": False,
        "ambiguous": False,
        "year_start": None,
        "year_end": None,
        "note": "",
    }
    if not raw:
        return out

    m = _RE_BETWEEN.search(raw)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        out.update(year_start=lo, year_end=hi, year=lo, precision="range",
                   qualifier="between", note="between {0} and {1}".format(lo, hi))
        return out

    m = _RE_BEFORE.search(raw)
    if m:
        y = int(m.group(1))
        out.update(year=y, year_start=None, year_end=y, precision="year",
                   qualifier="before", note="before {0}".format(y))
        return out

    m = _RE_AFTER.search(raw)
    if m:
        y = int(m.group(1))
        out.update(year=y, year_start=y, year_end=None, precision="year",
                   qualifier="after", note="after {0}".format(y))
        return out

    m = _RE_AGE.search(raw)
    if m:
        out.update(precision="age", note="stated age {0}".format(m.group(1)))
        return out

    m = _RE_ISO.search(raw)
    if m:
        out.update(year=int(m.group(1)), month=int(m.group(2)),
                   day=int(m.group(3)), precision="day")
        return out

    m = _RE_DMY.search(raw)
    if m and _month(m.group(2)):
        out.update(day=int(m.group(1)), month=_month(m.group(2)),
                   year=int(m.group(3)), precision="day")
        return out

    m = _RE_MDY.search(raw)
    if m and _month(m.group(1)):
        out.update(month=_month(m.group(1)), day=int(m.group(2)),
                   year=int(m.group(3)), precision="day")
        return out

    m = _RE_NUMERIC.search(raw)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:
            out.update(day=a, month=b, year=y, precision="day")
        elif b > 12 and a <= 12:
            out.update(month=a, day=b, year=y, precision="day")
        else:
            # Could be DD/MM or MM/DD - assume the US reading but flag it.
            out.update(month=a, day=b, year=y, precision="day", ambiguous=True,
                       note="numeric date is ambiguous between MM/DD and DD/MM")
        return out

    m = _RE_CIRCA.search(raw)
    if m:
        y = int(m.group(1))
        out.update(year=y, precision="year", circa=True,
                   note="circa {0}".format(y))
        return out

    m = _RE_DECADE.search(raw)
    if m:
        y = int(m.group(1))
        out.update(year=y, precision="decade", year_start=y, year_end=y + 9,
                   note="{0}s".format(y))
        return out

    m = _RE_MONTH_YEAR.search(raw)
    if m and _month(m.group(1)):
        out.update(month=_month(m.group(1)), year=int(m.group(2)),
                   precision="month")
        return out

    m = _RE_YEAR.search(raw)
    if m:
        out.update(year=int(m.group(1)), precision="year")
        return out

    return out


def date_year(d):
    """Best available year from a parsed date dict."""
    if not d:
        return None
    if d.get("year"):
        return d["year"]
    if d.get("year_start"):
        return d["year_start"]
    return None


def date_is_known(d):
    return bool(d) and d.get("precision") not in ("unknown", "") and bool(
        d.get("year") or d.get("year_start") or d.get("year_end"))


def format_date(d):
    if not d or d.get("precision") == "unknown":
        return "unknown"
    if d.get("precision") == "range":
        return "{0}-{1}".format(d.get("year_start"), d.get("year_end"))
    if d.get("precision") == "decade":
        return "{0}s".format(d.get("year"))
    if d.get("precision") == "age":
        return d.get("note") or "age stated"
    if d.get("precision") == "day" and d.get("day") and d.get("month"):
        return "{0} {1} {2}".format(d["day"], MONTH_NAMES[d["month"] - 1], d["year"])
    if d.get("precision") == "month" and d.get("month"):
        return "{0} {1}".format(MONTH_NAMES[d["month"] - 1], d.get("year"))
    if d.get("qualifier") == "before":
        return "before {0}".format(d.get("year"))
    if d.get("qualifier") == "after":
        return "after {0}".format(d.get("year"))
    if d.get("circa"):
        return "c. {0}".format(d.get("year"))
    return str(d.get("year") or "unknown")


# ---------------------------------------------------------------------------
# Place parsing
# ---------------------------------------------------------------------------

_COUNTY_WORDS = ("county", "parish", "borough", "township", "district",
                 "shire", "province", "prefecture", "municipality", "ward")

# Text that is clearly not a place.
_PLACE_NOISE = ("not sure", "unknown", "maybe", "perhaps", "somewhere",
                "thereabouts", "or there", "no idea", "?", "n/a", "tbd")


def parse_place(text):
    raw = (text or "").strip()
    out = {
        "raw": raw,
        "country": None,
        "state": None,
        "state_abbr": None,
        "county": None,
        "city": None,
        "precision": "unknown",
        "note": "",
    }
    if not raw:
        return out
    low = raw.lower()
    if any(token in low for token in _PLACE_NOISE):
        out["raw"] = ""
        out["note"] = "not a place: {0}".format(raw)
        return out
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return out

    # Walk from the tail looking for the most specific known jurisdiction.
    state = None
    state_abbr = None
    country = None
    county = None
    city = None
    consumed = 0

    for idx in range(len(parts) - 1, -1, -1):
        p = parts[idx].lower().rstrip(".")
        if p in US_STATES:
            state = parts[idx].title()
            state_abbr = US_STATES[p]
            country = country or "USA"
            consumed = idx + 1
            break
        if p in CANADA_PROVINCES:
            state = parts[idx].title()
            state_abbr = CANADA_PROVINCES[p]
            country = country or "Canada"
            consumed = idx + 1
            break
        if p in AU_STATES:
            state = parts[idx].title()
            state_abbr = AU_STATES[p]
            country = country or "Australia"
            consumed = idx + 1
            break
        if p in COUNTRIES:
            country = COUNTRIES[p]
            consumed = idx + 1
            break
        if p in UK_COUNTIES:
            county = parts[idx].title()
            country = country or UK_COUNTIES[p]
            consumed = idx + 1
            break

    head = parts[:consumed]
    tail = parts[consumed:]

    # A "X County" element that is not the tail (e.g. "Warren County, Ohio"
    # is already handled, but "Warren County" alone is not).
    if county is None:
        for idx, part in enumerate(head):
            low = part.lower()
            if any(w in low for w in _COUNTY_WORDS):
                county = part.title()
                consumed = idx + 1
                break

    city_candidates = parts[:consumed]
    if city_candidates:
        city = city_candidates[0].title()

    out.update(country=country, state=state, state_abbr=state_abbr,
               county=county, city=city)

    if country and state_abbr:
        out["precision"] = "state"
    elif country and county:
        out["precision"] = "county"
    elif country:
        out["precision"] = "country"
    elif state or county or city:
        out["precision"] = "locality"
    if country == "USA" and state_abbr:
        admitted = STATE_ADMISSION_YEAR.get(state_abbr)
        if admitted:
            out["state_admitted"] = admitted
    return out


def place_country_from_text(text):
    low = (text or "").lower()
    for key, val in COUNTRIES.items():
        if key in low:
            return val
    return None


def place_state_from_text(text):
    low = (text or "").lower()
    for key, val in US_STATES.items():
        if re.search(r"\b" + re.escape(key) + r"\b", low):
            return key.title(), val
    return None, None


def format_place(p):
    if not p or not p.get("raw"):
        return "unknown"
    return p["raw"]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

_TITLES = {"mr", "mrs", "miss", "ms", "dr", "rev", "reverend", "sir", "lady",
           "capt", "captain", "col", "colonel", "gen", "general", "prof",
           "professor", "sgt", "hon", "father", "mother", "brother", "sister"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "esq", "esquire", "phd", "md"}


def normalize_name(name):
    """Return a normalised key for a personal name."""
    if not name:
        return ""
    text = name.strip().lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\b(?:b|d)\.?\s*\d{4}\b", " ", text)
    text = text.replace(",", " ")
    text = re.sub(r"[^a-z0-9\s'-]", " ", text)
    tokens = [t for t in text.split() if t]
    tokens = [t for t in tokens if t not in _TITLES and t.strip("'-") not in _SUFFIXES]
    tokens = [t for t in tokens if t not in ("born", "died", "b", "d")]
    tokens = [t for t in tokens if not t.isdigit()]
    if not tokens:
        return ""
    return " ".join(tokens)


def name_parts(name):
    n = normalize_name(name)
    if not n:
        return "", "", ""
    bits = n.split()
    first = bits[0]
    last = bits[-1]
    middle = " ".join(bits[1:-1])
    return first, middle, last


def name_surname(name):
    return name_parts(name)[2]


def looks_like_name(text):
    if not text:
        return False
    t = text.strip()
    if len(t) < 3 or len(t) > 60:
        return False
    if re.match(r"^(born|died|married|buried|source|sources|note|notes|conflict)\b", t, re.I):
        return False
    if not re.search(r"[A-Za-z]{2}", t):
        return False
    words = t.split()
    if not (1 <= len(words) <= 6):
        return False
    if any(w.lower().endswith(":") for w in words):
        return False
    return True


# ---------------------------------------------------------------------------
# Source detection
# ---------------------------------------------------------------------------

_TYPE_BY_KEYWORD = {}
for _key, _profile in SOURCE_TYPES.items():
    for _kw in _profile["keywords"]:
        _TYPE_BY_KEYWORD.setdefault(_kw, _key)
    _TYPE_BY_KEYWORD.setdefault(_key, _key)
# More specific phrases win - keep the longest keyword when they collide.
_KEYWORD_SORTED = sorted(_TYPE_BY_KEYWORD.items(), key=lambda kv: -len(kv[0]))


def detect_source_type(text):
    """Return (type_key, matched_keyword) or (None, None).

    The earliest keyword in the citation wins, so "1880 US Census, Tarlton
    Township" is a census and not a land record.
    """
    low = (text or "").lower()
    if not low:
        return None, None
    best = None
    for keyword, type_key in _KEYWORD_SORTED:
        pos = low.find(keyword)
        if pos < 0:
            continue
        if best is None or pos < best[0]:
            best = (pos, keyword, type_key)
    if best is None:
        return None, None
    return best[2], best[1]


def source_year(text):
    m = _RE_YEAR.search(text or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Records / claims
# ---------------------------------------------------------------------------

EVENT_LABELS = {
    "born": "birth", "birth": "birth", "birth date": "birth",
    "birthdate": "birth", "date of birth": "birth", "b.": "birth",
    "baptized": "baptism", "baptised": "baptism", "baptism": "baptism",
    "christened": "baptism", "christening": "baptism",
    "died": "death", "death": "death", "death date": "death",
    "date of death": "death", "d.": "death", "died on": "death",
    "buried": "burial", "burial": "burial", "buried at": "burial",
    "interred": "burial", "burial place": "burial", "cemetery": "burial",
    "married": "marriage", "marriage": "marriage", "marriage date": "marriage",
    "married date": "marriage", "wedding": "marriage", "wed": "marriage",
    "residence": "residence", "resided": "residence", "lived": "residence",
    "address": "residence", "home": "residence", "moved to": "residence",
    "emigrated": "migration", "immigrated": "migration",
    "migration": "migration", "arrived": "migration",
    "occupation": "occupation", "occupation/profession": "occupation",
    "work": "occupation", "profession": "occupation", "trade": "occupation",
    "name": "name", "full name": "name", "person": "name",
}

RELATION_LABELS = {
    "father": "father", "father's name": "father", "father/father": "father",
    "dad": "father", "mother": "mother", "mother's name": "mother",
    "mom": "mother", "spouse": "spouse", "wife": "spouse", "husband": "spouse",
    "married to": "spouse", "children": "child", "child": "child",
    "sons and daughters": "child", "son": "child", "daughter": "child",
    "also known as": "alias", "aka": "alias", "alias": "alias",
    "aliases": "alias", "maiden name": "alias", "nee": "alias", "née": "alias",
    "nickname": "alias", "formerly": "alias", "later known as": "alias",
}

SOURCE_LABELS = {"source", "sources", "citation", "citations", "evidence",
                 "evidence:", "ref", "refs", "reference", "references",
                 "record", "records", "document", "documents"}

NOTE_LABELS = {"note", "notes", "comment", "comments", "remark", "remarks",
               "story", "background", "context", "info", "information"}

CONFLICT_LABELS = {"conflict", "conflicts", "contradiction", "issue",
                   "issues", "problem", "discrepancy"}

_RE_LABEL = re.compile(r"^\s*([^:]{1,32}?)\s*:\s*(.+?)\s*$")
_RE_PROSE_EVENT = re.compile(
    r"\b(born|birth\s+date|died|death\s+date|buried|interred|married|"
    r"christened|baptized|moved\s+to|emigrated|immigrated)\b"
    r"[^\dA-Za-z]{0,12}([^,;\n]{3,80})", re.I)

_VERB_TO_TYPE = {
    "born": "birth", "birth date": "birth", "died": "death",
    "death date": "death", "buried": "burial", "interred": "burial",
    "married": "marriage", "christened": "baptism", "baptized": "baptism",
    "moved to": "residence", "emigrated": "migration",
    "immigrated": "migration",
}


def _find_date_span(text):
    """Find the first date expression in text.

    Returns (matched_text, start, end) or None. Earlier matches win; ties are
    broken by the longer match, so '12 March 1872' beats the bare '1872'.
    """
    patterns = [
        _RE_BETWEEN, _RE_BEFORE, _RE_AFTER, _RE_CIRCA, _RE_ISO,
        _RE_DMY, _RE_MDY, _RE_NUMERIC, _RE_DECADE, _RE_MONTH_YEAR, _RE_YEAR,
    ]
    best = None
    for rx in patterns:
        m = rx.search(text)
        if not m:
            continue
        if best is None or m.start() < best[1]:
            best = (m.group(0), m.start(), m.end())
    return best


def _split_value_date_place(value):
    """Split a value like '12 Mar 1872, Warren County, Ohio' into date + place."""
    value = (value or "").strip()
    if not value:
        return "", ""
    if _RE_AGE.search(value) and not _RE_YEAR.search(value):
        return value, ""
    span = _find_date_span(value)
    if not span:
        return "", value
    matched, start, end = span
    rest = (value[:start] + " " + value[end:]).strip()
    semi = rest.find(";")
    if semi >= 0:
        rest = rest[:semi]
    nxt = _RE_YEAR.search(rest)
    if nxt:
        rest = rest[:nxt.start()]
    rest = rest.strip(" \t-\u2013\u2014,;:")
    low = rest.lower()
    if low.startswith("at "):
        rest = rest[3:]
    elif low.startswith("in "):
        rest = rest[3:]
    rest = rest.strip(" \t-\u2013\u2014,;:")
    return matched, rest


def split_records(text):
    """Split notes into one raw block per person claim."""
    blocks = []
    current = []
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if stripped in ("---", "----", "===", "___"):
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [b.strip() for b in blocks if b.strip()]


def parse_record(block, index):
    """Parse one block of notes into a claim record."""
    record = {
        "index": index,
        "name_raw": "",
        "name_norm": "",
        "events": [],
        "relations": [],
        "sources": [],
        "notes": [],
        "conflicts": [],
        "raw": block,
    }
    lines = block.splitlines()
    last_event = None
    pending_inline_source = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        m = _RE_LABEL.match(line)
        label = m.group(1).strip().lower() if m else ""
        value = m.group(2).strip() if m else ""

        if m and label in SOURCE_LABELS:
            for chunk in re.split(r";\s*|\n", value):
                chunk = chunk.strip()
                if not chunk:
                    continue
                stype, kw = detect_source_type(chunk)
                record["sources"].append({
                    "raw": chunk,
                    "type": stype,
                    "keyword": kw,
                    "tier": SOURCE_TYPES[stype]["tier"] if stype else None,
                    "label": SOURCE_TYPES[stype]["label"] if stype else "Unclassified source",
                    "reliability": SOURCE_TYPES[stype]["reliability"] if stype else 40,
                    "year": source_year(chunk),
                    "attached_event": last_event["type"] if last_event else None,
                })
            continue

        if m and label in CONFLICT_LABELS:
            record["conflicts"].append(value)
            continue

        if m and label in NOTE_LABELS:
            record["notes"].append(value)
            continue

        if m and label in RELATION_LABELS:
            rel_type = RELATION_LABELS[label]
            for person in re.split(r";\s*|\band\s+", value):
                person = person.strip()
                if person:
                    record["relations"].append({
                        "type": rel_type,
                        "raw": person,
                        "name_norm": normalize_name(person),
                    })
            continue

        if m and label in EVENT_LABELS:
            etype = EVENT_LABELS[label]
            if etype == "name":
                record["name_raw"] = value
                continue
            date_seg, place_seg = _split_value_date_place(value)
            event = {
                "type": etype,
                "raw": value,
                "date": parse_date(date_seg) if date_seg else parse_date(""),
                "place": parse_place(place_seg),
                "sources": [],
            }
            record["events"].append(event)
            last_event = event
            continue

        if m and len(label) > 3 and label not in EVENT_LABELS:
            # Unknown labelled line - keep the value as a note but still try
            # to pull a source out of it.
            record["notes"].append("{0}: {1}".format(label, value))
            stype, kw = detect_source_type(value)
            if stype:
                record["sources"].append({
                    "raw": "{0}: {1}".format(label, value),
                    "type": stype,
                    "keyword": kw,
                    "tier": SOURCE_TYPES[stype]["tier"],
                    "label": SOURCE_TYPES[stype]["label"],
                    "reliability": SOURCE_TYPES[stype]["reliability"],
                    "year": source_year(value),
                    "attached_event": None,
                })
            continue

        # Free prose line.
        if not record["name_raw"] and not record["events"] and looks_like_name(line):
            record["name_raw"] = line
            continue

        stype, kw = detect_source_type(line)
        if stype:
            record["sources"].append({
                "raw": line,
                "type": stype,
                "keyword": kw,
                "tier": SOURCE_TYPES[stype]["tier"],
                "label": SOURCE_TYPES[stype]["label"],
                "reliability": SOURCE_TYPES[stype]["reliability"],
                "year": source_year(line),
                "attached_event": None,
            })
            continue

        pm = _RE_PROSE_EVENT.search(line)
        if pm:
            etype = _VERB_TO_TYPE.get(pm.group(1).lower())
            if etype:
                tail = pm.group(2).strip(" .")
                date_seg, place_seg = _split_value_date_place(tail)
                event = {
                    "type": etype,
                    "raw": line,
                    "date": parse_date(date_seg) if date_seg else parse_date(""),
                    "place": parse_place(place_seg),
                    "sources": [],
                }
                record["events"].append(event)
                last_event = event
                continue

        record["notes"].append(line)

    # Attach record-level sources to events when the source clearly belongs
    # to that event (the source line directly followed the event line).
    for src in record["sources"]:
        if src.get("attached_event"):
            for event in record["events"]:
                if event["type"] == src["attached_event"]:
                    event["sources"].append(src["raw"])

    if not record["name_raw"]:
        for event in record["events"]:
            if event["type"] == "birth" and event["place"].get("raw"):
                break

    record["name_norm"] = normalize_name(record["name_raw"])
    return record


def parse_notes(text):
    """Parse a whole block of research notes into claim records."""
    blocks = split_records(text)
    records = [parse_record(b, i) for i, b in enumerate(blocks)]
    return records
