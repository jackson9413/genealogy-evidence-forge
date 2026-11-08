"""Analysis engine for genealogy-evidence-forge.

Takes parsed claim records and produces:
  * evidence findings (45+ rules across 5 axes) with evidence + a fix
  * a 0-100 evidence-readiness scorecard with structural caps
  * identity analysis (same-person / same-name-collision)
  * a prioritised research plan naming real record collections
  * Markdown / CSV / JSON exports

Pure standard library. No network calls, no API keys, no LLM.
"""

import csv
import io
import json
import re

from gh_parser import (
    CURRENT_YEAR,
    date_is_known,
    date_year,
    format_date,
    format_place,
    name_parts,
    normalize_name,
    parse_notes,
)
from gh_sources import (
    COLLECTIONS,
    COMMON_SURNAMES,
    DERIVATIVE_TYPES,
    SOURCE_TYPES,
)

AXES = ["sourcing", "consistency", "coverage", "disambiguation", "researchability"]

AXIS_LABELS = {
    "sourcing": "Sourcing",
    "consistency": "Consistency",
    "coverage": "Coverage",
    "disambiguation": "Disambiguation",
    "researchability": "Researchability",
}

AXIS_DESCRIPTIONS = {
    "sourcing": "How good the cited evidence actually is, and whether the core events have any evidence at all.",
    "consistency": "Whether the dates, places and relationships in the claim can all be true at the same time.",
    "coverage": "How much of the person's life is documented rather than assumed.",
    "disambiguation": "How confident you can be that this record is about one person and not a same-name collision.",
    "researchability": "Whether the next step is actually findable in a collection that covers that time and place.",
}

MODES = {
    "standard": {
        "label": "Standard family research",
        "weights": {"sourcing": 0.25, "consistency": 0.25, "coverage": 0.20,
                    "disambiguation": 0.15, "researchability": 0.15},
    },
    "professional": {
        "label": "Professional standard",
        "weights": {"sourcing": 0.30, "consistency": 0.25, "coverage": 0.20,
                    "disambiguation": 0.10, "researchability": 0.15},
    },
    "family_story": {
        "label": "Family story (keep it human)",
        "weights": {"sourcing": 0.18, "consistency": 0.18, "coverage": 0.22,
                    "disambiguation": 0.15, "researchability": 0.27},
    },
    "dna": {
        "label": "DNA-led research",
        "weights": {"sourcing": 0.30, "consistency": 0.20, "coverage": 0.15,
                    "disambiguation": 0.25, "researchability": 0.10},
    },
}

VERDICT_BANDS = [
    (85, "research_ready", "Research-ready - the claim is sourced and the next step is obvious."),
    (70, "solid_progress", "Solid progress - mostly evidenced, with a few gaps worth closing."),
    (55, "needs_work", "Needs work - real gaps remain before this claim can be relied on."),
    (40, "thin_evidence", "Thin evidence - more family story than record so far."),
    (0, "speculation", "Speculation - treat every detail here as a hypothesis."),
]

# Which event types each record-collection kind can actually speak to.
_COLLECTION_EVENTS = {
    "census": ["residence", "birth", "parent", "migration"],
    "vital": ["birth", "marriage", "death"],
    "military": ["birth", "residence", "occupation", "migration"],
    "immigration": ["migration", "birth", "residence", "parent"],
    "probate": ["death", "spouse", "child", "parent", "residence"],
    "newspaper": ["death", "marriage", "birth", "residence"],
    "cemetery": ["burial", "death"],
    "church": ["birth", "baptism", "marriage", "death", "burial", "parent"],
    "land": ["residence", "migration", "occupation"],
    "court": ["birth", "death", "parent", "spouse", "residence", "migration"],
    "private": ["birth", "death", "marriage", "residence", "parent", "child"],
    "interview": ["birth", "death", "marriage", "residence", "parent", "migration"],
    "dna": ["parent"],
}

_COUNTRY_FAMILY = {
    "England": ["England", "Wales", "Scotland", "Ireland"],
    "Wales": ["England", "Wales"],
    "Scotland": ["Scotland"],
    "Ireland": ["Ireland"],
    "USA": ["USA"],
    "Canada": ["Canada"],
    "Australia": ["Australia"],
}


def _finding(fid, axis, severity, title, detail, evidence, fix, record_index=None,
             person=""):
    return {
        "id": fid,
        "axis": axis,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": evidence,
        "fix": fix,
        "record_index": record_index,
        "person": person,
    }


def _event_of(record, etype):
    for ev in record["events"]:
        if ev["type"] == etype:
            return ev
    return None


def _events_of(record, etype):
    return [ev for ev in record["events"] if ev["type"] == etype]


def _label_for(record):
    return record["name_raw"] or "Unnamed person #{0}".format(record["index"] + 1)


# ---------------------------------------------------------------------------
# Collection selection
# ---------------------------------------------------------------------------

def _collection_covers_year(collection, year):
    if year is None:
        return True
    if "available_years" in collection:
        return year in collection["available_years"]
    rng = collection.get("year_range")
    if rng:
        return rng[0] <= year <= rng[1]
    return True


def _collection_country_ok(collection, place):
    c_country = collection.get("country")
    if c_country == "GLOBAL":
        return True
    p_country = place.get("country")
    if not p_country:
        return True
    family = _COUNTRY_FAMILY.get(c_country, [c_country])
    return p_country in family


def _collection_lost_year(collection, year):
    if year is None:
        return None
    lost = collection.get("lost_years") or {}
    return lost.get(year)


def suggest_collections(event_type, year, place, limit=4):
    """Return the record collections most likely to settle this event."""
    out = []
    for c in COLLECTIONS:
        kinds = _COLLECTION_EVENTS.get(c.get("kind"), [])
        if event_type not in kinds:
            continue
        if not _collection_country_ok(c, place):
            continue
        if not _collection_covers_year(c, year):
            continue
        lost = _collection_lost_year(c, year)
        if lost:
            continue
        score = c.get("specificity", 60)
        if c.get("kind") == "vital" and event_type in ("birth", "marriage", "death"):
            score += 6
        if c.get("kind") == "census" and event_type == "residence":
            score += 6
        if c.get("kind") in ("private", "interview"):
            score -= 8
        out.append({
            "id": c["id"],
            "name": c["name"],
            "kind": c.get("kind"),
            "country": c.get("country"),
            "score": score,
            "year_note": c.get("year_note"),
            "proves": c.get("proves"),
            "how_to": c.get("how_to"),
            "caveats": c.get("caveats", []),
        })
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def census_targets(birth_year, death_year, place):
    """Census years worth pulling for a person, with the age they'd be."""
    targets = []
    if not birth_year:
        return targets
    country = place.get("country")
    if country == "USA":
        years = [1790, 1800, 1810, 1820, 1830, 1840, 1850, 1860, 1870,
                 1880, 1900, 1910, 1920, 1930, 1940, 1950]
    elif country in ("England", "Wales", "Scotland", "Ireland"):
        years = [1841, 1851, 1861, 1871, 1881, 1891, 1901, 1911, 1921, 1939, 1951]
    elif country == "Canada":
        years = [1871, 1881, 1891, 1901, 1911, 1921, 1941, 1951]
    elif country == "Australia":
        years = [1911, 1921, 1933, 1948]
    else:
        # No census series in the reference covers this country.
        return []
    for y in years:
        age = y - birth_year
        if age < 0 or age > 90:
            continue
        if death_year and y > death_year + 1:
            continue
        targets.append((y, age))
    return targets


# ---------------------------------------------------------------------------
# Identity analysis
# ---------------------------------------------------------------------------

def _record_birth_year(record):
    ev = _event_of(record, "birth")
    return date_year(ev["date"]) if ev else None


def _record_death_year(record):
    ev = _event_of(record, "death")
    return date_year(ev["date"]) if ev else None


def _record_places(record):
    out = []
    for ev in record["events"]:
        p = ev["place"]
        if p.get("county") or p.get("state") or p.get("city"):
            out.append(p)
    return out


def identity_pairs(records):
    """Compare records pairwise for same-person and same-name-collision risk."""
    pairs = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            na, nb = a.get("name_norm"), b.get("name_norm")
            if not na or not nb:
                continue
            fa, ma, la = name_parts(na)
            fb, mb, lb = name_parts(nb)
            if la != lb:
                continue
            score = 0
            reasons = []
            conflicts = []
            if fa == fb:
                score += 45
                reasons.append("same first name ({0})".format(fa))
            elif fa and fb and fa[0] == fb[0]:
                score += 26
                reasons.append("same first initial ({0})".format(fa[0]))
            else:
                score -= 10
            if ma and mb:
                if ma == mb:
                    score += 12
                    reasons.append("same middle name ({0})".format(ma))
                elif ma[0] == mb[0]:
                    score += 6
                else:
                    score -= 8
            by_a, by_b = _record_birth_year(a), _record_birth_year(b)
            gap = _years_apart(by_a, by_b)
            if gap is not None:
                if gap == 0:
                    score += 25
                    reasons.append("identical birth year")
                elif gap <= 2:
                    score += 18
                    reasons.append("birth years within {0} year(s)".format(gap))
                elif gap <= 6:
                    score += 8
                    reasons.append("birth years {0} years apart".format(gap))
                elif gap <= 12:
                    score -= 5
                else:
                    score -= 28
                    conflicts.append("birth years {0} years apart - probably two different people".format(gap))
            dy_a, dy_b = _record_death_year(a), _record_death_year(b)
            gapd = _years_apart(dy_a, dy_b)
            if gapd is not None:
                if gapd == 0:
                    score += 20
                    reasons.append("identical death year")
                elif gapd <= 3:
                    score += 12
                elif gapd > 15:
                    score -= 22
                    conflicts.append("death years {0} years apart".format(gapd))
            pa = _record_places(a)
            pb = _record_places(b)
            shared = set()
            for x in pa:
                for y in pb:
                    if x.get("county") and x.get("county") == y.get("county"):
                        shared.add(x["county"])
                    if x.get("state_abbr") and x.get("state_abbr") == y.get("state_abbr"):
                        shared.add(x.get("state"))
            if shared:
                score += 10
                reasons.append("shared place ({0})".format(
                    ", ".join(sorted(s for s in shared if s))))
            rel_a = {r["name_norm"] for r in a["relations"]}
            rel_b = {r["name_norm"] for r in b["relations"]}
            shared_rel = rel_a & rel_b
            if shared_rel:
                score += 16
                reasons.append("same relative named ({0})".format(
                    ", ".join(sorted(shared_rel))))
            if score >= 75:
                band = "likely_same"
            elif score >= 45:
                band = "possible_same"
            else:
                band = "probably_different"
            pairs.append({
                "a": i, "b": j, "score": max(0, min(100, score)), "band": band,
                "reasons": reasons, "conflicts": conflicts,
            })
    return pairs


def _years_apart(a, b):
    if a is None or b is None:
        return None
    return abs(a - b)


# ---------------------------------------------------------------------------
# Per-record checks
# ---------------------------------------------------------------------------

def _flip_date(d):
    out = dict(d)
    if out.get("day") and out.get("month"):
        out["day"], out["month"] = out["month"], out["day"]
        out["ambiguous"] = False
    return out


def _check_timeline(record, findings):
    person = _label_for(record)
    idx = record["index"]
    births = _events_of(record, "birth")
    deaths = _events_of(record, "death")
    marriages = _events_of(record, "marriage")
    baptisms = _events_of(record, "baptism")
    burials = _events_of(record, "burial")
    residences = _events_of(record, "residence")
    migrations = _events_of(record, "migration")

    by = date_year(births[0]["date"]) if births else None
    dy = date_year(deaths[0]["date"]) if deaths else None

    if by and dy and by > dy:
        findings.append(_finding(
            "birth_after_death", "consistency", "high",
            "Birth date is after the death date",
            "The recorded birth year comes after the recorded death year, so the two dates cannot both be right.",
            "birth {0} / death {1}".format(by, dy),
            "Re-read both records and check whether the birth year belongs to a parent, not to this person.",
            idx, person))

    if by and dy:
        age = dy - by
        if age > 112:
            findings.append(_finding(
                "implausible_lifespan", "consistency", "high",
                "Lifespan of {0} years is not credible".format(age),
                "A lifespan above 112 years is almost always a mis-transcribed year rather than a real fact.",
                "birth {0} / death {1}".format(by, dy),
                "Look for an age at death in a probate file or obituary and rebuild both years from it.",
                idx, person))

    for m in marriages:
        my = date_year(m["date"])
        if my is None:
            continue
        if by and my < by:
            findings.append(_finding(
                "marriage_before_birth", "consistency", "high",
                "Marriage date precedes the birth date",
                "The marriage is dated before the person was born, so at least one date is wrong.",
                "marriage {0} / birth {1}".format(my, by),
                "Check whether the marriage belongs to a parent, and whether the birth year was copied from a later record.",
                idx, person))
        if dy and my > dy:
            findings.append(_finding(
                "marriage_after_death", "consistency", "high",
                "Marriage date follows the death date",
                "The person is recorded as marrying after their own death.",
                "marriage {0} / death {1}".format(my, dy),
                "Re-read the marriage record - a remarriage after a spouse's death is normal, but not after the person's own.",
                idx, person))

    for ev in births[1:] + deaths[1:] + baptisms + burials + residences + migrations:
        ey = date_year(ev["date"])
        if ey is None:
            continue
        if by and ey < by:
            findings.append(_finding(
                "event_before_birth", "consistency", "medium",
                "A {0} event predates the birth".format(ev["type"]),
                "An event is dated before the person was born, which usually means it belongs to a parent.",
                "{0} {1} / birth {2}".format(ev["type"], ey, by),
                "Move the event to the parent's record or correct the year.",
                idx, person))
        if dy and ey > dy and ev["type"] != "burial":
            findings.append(_finding(
                "event_after_death", "consistency", "medium",
                "A {0} event postdates the death".format(ev["type"]),
                "The event happens after the person died, so it is either about someone else or the death year is wrong.",
                "{0} {1} / death {2}".format(ev["type"], ey, dy),
                "Check the sequence of the family moves - a residence after death usually belongs to a child.",
                idx, person))

    for b in burials:
        byy = date_year(b["date"])
        if byy and dy and byy < dy:
            findings.append(_finding(
                "burial_before_death", "consistency", "medium",
                "Burial is dated before the death",
                "The interment happens before the recorded death, which usually means the death year was copied wrong.",
                "burial {0} / death {1}".format(byy, dy),
                "Treat the burial date as the firmer death date and look for a death record in that year.",
                idx, person))

    for b in burials:
        byy = date_year(b["date"])
        if byy and dy and byy - dy >= 2:
            findings.append(_finding(
                "burial_long_after_death", "consistency", "medium",
                "Burial is {0} years after the death".format(byy - dy),
                "A gap this wide between the recorded death and the recorded burial usually means the two events belong to different people, or one date is wrong.",
                "death {0} / burial {1}".format(dy, byy),
                "Check the cemetery register against the death record - a reused plot or a misfiled entry will show up there.",
                idx, person))

    for etype in ("birth", "death", "marriage", "burial"):
        evs = _events_of(record, etype)
        if len(evs) < 2:
            continue
        known = [(e, date_year(e["date"])) for e in evs]
        known = [(e, y) for e, y in known if y is not None]
        if len(known) >= 2:
            spread = max(y for _, y in known) - min(y for _, y in known)
            if spread >= 1:
                findings.append(_finding(
                    "conflicting_dates", "consistency", "high",
                    "Conflicting {0} dates".format(etype),
                    "Two {0} dates for the same person differ by {1} years. One of them is not this person's.".format(etype, spread),
                    "; ".join("{0} ({1})".format(format_date(e["date"]), e["raw"]) for e, _ in known),
                    "Read the two original records and decide which one names the same person - same parents, same spouse, same town.",
                    idx, person))

    for etype in ("birth", "death"):
        evs = _events_of(record, etype)
        places = [e["place"] for e in evs if e["place"].get("raw")]
        uniq = set(p["raw"].lower() for p in places)
        if len(uniq) > 1:
            findings.append(_finding(
                "conflicting_places", "consistency", "medium",
                "Conflicting {0} places".format(etype),
                "The {0} is given in more than one place.".format(etype),
                "; ".join(p["raw"] for p in places),
                "Two places for one {0} usually means two people with the same name, or a move recorded as a birth.".format(etype),
                idx, person))

    for ev in record["events"]:
        if ev["date"].get("ambiguous"):
            findings.append(_finding(
                "ambiguous_date_format", "consistency", "medium",
                "Numeric date could be read two ways",
                "'{0}' can mean {1} or {2}. One reading is impossible for this person.".format(
                    ev["date"]["raw"], format_date(ev["date"]), format_date(_flip_date(ev["date"]))),
                ev["raw"],
                "Write the date in words (12 March 1872) so the reading is unambiguous, and check the original record.",
                idx, person))

    for ev in record["events"]:
        y = date_year(ev["date"])
        if y and (y < 1500 or y > CURRENT_YEAR):
            findings.append(_finding(
                "date_out_of_range", "consistency", "high",
                "Date of {0} is outside a credible range".format(ev["type"]),
                "{0} is not a possible year for a life event.".format(y),
                ev["raw"],
                "Check for a digit transposition in the source, and look for the same event in an adjacent year.",
                idx, person))

    if by:
        for rel in record["relations"]:
            if rel["type"] not in ("father", "mother"):
                continue
            m = re.search(r"\b(1[5-9]\d{2}|20[0-2]\d)\b", rel["raw"])
            if not m:
                continue
            py = int(m.group(1))
            gap = by - py
            if rel["type"] == "mother":
                lo, hi = 15, 55
            else:
                lo, hi = 16, 70
            if gap < 0:
                findings.append(_finding(
                    "parent_younger_than_child", "consistency", "high",
                    "Parent is younger than the child",
                    "The {0} was born {1} years after the child's birth year.".format(rel["type"], abs(gap)),
                    "{0}: {1} / child born {2}".format(rel["type"], rel["raw"], by),
                    "Check whether the name belongs to a grandparent or to the child's own child.",
                    idx, person))
            elif gap < lo:
                findings.append(_finding(
                    "parent_too_young", "consistency", "medium",
                    "{0} was only {1} at the child's birth".format(rel["type"].title(), gap),
                    "A {0} of {1} is possible but unusual, and usually signals a copied year.".format(rel["type"], gap),
                    "{0}: {1} / child born {2}".format(rel["type"], rel["raw"], by),
                    "Confirm with a second record before you rely on this line of descent.",
                    idx, person))
            elif gap > hi:
                findings.append(_finding(
                    "parent_too_old", "consistency", "medium",
                    "{0} was {1} at the child's birth".format(rel["type"].title(), gap),
                    "A {0} of {1} is highly unlikely and usually means the year belongs to the wrong generation.".format(rel["type"], gap),
                    "{0}: {1} / child born {2}".format(rel["type"], rel["raw"], by),
                    "Walk the generation back one step - this parent may actually be a grandparent.",
                    idx, person))
    return findings


def _check_sources(record, findings):
    person = _label_for(record)
    idx = record["index"]
    sources = record["sources"]
    birth = _event_of(record, "birth")
    death = _event_of(record, "death")

    if not sources:
        findings.append(_finding(
            "no_sources", "sourcing", "high",
            "No source cited for this person",
            "Every detail on this record is currently unsourced - it is a story, not a finding.",
            record["raw"][:240],
            "Cite the record that first mentioned this person, even if it is only a family Bible or an obituary.",
            idx, person))
    else:
        types = [s["type"] for s in sources if s["type"]]
        if types and all(t in DERIVATIVE_TYPES for t in types):
            findings.append(_finding(
                "derivative_sources_only", "sourcing", "high",
                "Only derivative sources cited",
                "Every citation points at an online tree, which is a copy of somebody else's work - not evidence.",
                "; ".join(s["raw"] for s in sources[:4]),
                "Open the tree, find the original record it points to, and cite that record instead.",
                idx, person))
        if not types:
            findings.append(_finding(
                "unclassified_sources", "sourcing", "medium",
                "Sources could not be classified",
                "The citations do not look like any known record type, so their reliability cannot be judged.",
                "; ".join(s["raw"] for s in sources[:4]),
                "Name the record type and the repository, for example 'Warren County Probate, file 1234'.",
                idx, person))
        if len(set(types)) == 1 and len(sources) > 1:
            findings.append(_finding(
                "single_source_type", "sourcing", "low",
                "All evidence is the same kind of record",
                "One record type repeated is not independent corroboration - a copied entry looks the same.",
                "; ".join(s["raw"] for s in sources[:4]),
                "Add a record of a different type (a vital record against a census, say) so the two can agree or disagree.",
                idx, person))
        for s in sources:
            if s["type"] == "online_tree":
                findings.append(_finding(
                    "online_tree_citation", "sourcing", "medium",
                    "An online tree is cited as a source",
                    "An online collaborative tree is a hypothesis someone published, not a record.",
                    s["raw"],
                    "Follow the tree's citation back to the original record and cite that.",
                    idx, person))
                break
        if types and all(t == "interview" for t in types):
            findings.append(_finding(
                "oral_tradition_only", "sourcing", "high",
                "Only oral tradition supports this claim",
                "Family memory is a good lead and a poor proof - dates drift by decades.",
                "; ".join(s["raw"] for s in sources[:3]),
                "Use the memory as a search plan: take the name, the place and the decade to a parish register or a census.",
                idx, person))

    if (sources and all(s["type"] == "census" for s in sources)
            and birth and birth["date"].get("precision") == "day"):
        findings.append(_finding(
            "census_exact_birth_date", "sourcing", "medium",
            "A census is the only evidence for an exact birth date",
            "Census schedules record an age, not a birth date, so an exact date taken from a census was supplied by someone else.",
            "{0} / birth {1}".format(sources[0]["raw"], format_date(birth["date"])),
            "Check for a birth certificate, a delayed birth registration or a Social Security application instead.",
            idx, person))

    by = date_year(birth["date"]) if birth else None
    dy = date_year(death["date"]) if death else None
    for s in sources:
        sy = s.get("year")
        if sy is None:
            continue
        if by and sy < by - 1 and s["type"] not in ("private", "interview", "online_tree"):
            findings.append(_finding(
                "source_predates_event", "consistency", "high",
                "A source predates the event it is cited for",
                "The source was created in {0}, before the {1} it is being used to prove.".format(
                    sy, s.get("attached_event") or "event"),
                s["raw"],
                "The event date is probably wrong, or the citation belongs to a different person.",
                idx, person))
            break

    if (sources and all(s["type"] == "newspaper" for s in sources)
            and birth and birth["date"].get("precision") == "day"):
        findings.append(_finding(
            "obituary_used_for_birth", "sourcing", "medium",
            "A newspaper is the only evidence for a precise birth date",
            "Obituaries are written by survivors and are unreliable for birth details.",
            sources[0]["raw"],
            "Confirm the birth with a register, a certificate or a draft registration card.",
            idx, person))

    graves = [s for s in sources if s["type"] == "grave"]
    if graves and len(graves) == len(sources):
        findings.append(_finding(
            "memorial_only", "sourcing", "medium",
            "A user-built memorial is the only source",
            "Memorial pages are often copied from another tree, so the error is inherited as well as the claim.",
            "; ".join(s["raw"] for s in graves[:3]),
            "Use the page to find the cemetery and the inscription, then look for a cemetery register or a death certificate.",
            idx, person))

    if record.get("conflicts"):
        for note in record["conflicts"]:
            findings.append(_finding(
                "explicit_conflict", "consistency", "high",
                "You have flagged a conflict on this claim",
                "The notes themselves record a contradiction: {0}".format(note),
                note,
                "Put the two versions side by side and decide which record names the same person - parents, spouse and town are the tests.",
                idx, person))

    if sources and all(s.get("year") is None for s in sources):
        findings.append(_finding(
            "source_without_year", "sourcing", "low",
            "Citations carry no year",
            "A source without a year cannot be placed in a sequence, so it cannot confirm or contradict anything.",
            "; ".join(s["raw"] for s in sources[:3]),
            "Add the record's own year - a census year, a certificate date, a burial date.",
            idx, person))
    return findings


def _check_coverage(record, findings):
    person = _label_for(record)
    idx = record["index"]
    birth = _event_of(record, "birth")
    death = _event_of(record, "death")
    rels = {r["type"] for r in record["relations"]}
    types = {e["type"] for e in record["events"]}

    if not birth or not date_is_known(birth["date"]):
        findings.append(_finding(
            "missing_birth_date", "coverage", "high",
            "No birth date recorded",
            "Without a birth year you cannot be placed in a census, matched to a register or checked against another record.",
            record["raw"][:200],
            "Get a birth year from any record that states one: a death certificate, a draft card, a probate file, a gravestone.",
            idx, person))
    elif not birth["place"].get("raw"):
        findings.append(_finding(
            "missing_birth_place", "coverage", "high",
            "No birth place recorded",
            "The birth year without a place is half a search - the registers are filed by place.",
            record["raw"][:200],
            "Recover the county and state (or parish) from a birth record, an obituary, a gravestone or the 1900+ census birthplace column.",
            idx, person))
    elif birth["place"].get("precision") == "country":
        findings.append(_finding(
            "birth_place_too_broad", "coverage", "medium",
            "Birth place is only a country",
            "A country-level birthplace will not narrow a register search.",
            birth["place"]["raw"],
            "Push for county and town - even a county and a state halves the record set you must search.",
            idx, person))

    if not death or not date_is_known(death["date"]):
        findings.append(_finding(
            "missing_death_date", "coverage", "medium",
            "No death date recorded",
            "Without a death year the person cannot be found in probate, obituaries or cemetery registers.",
            record["raw"][:200],
            "Search probate indexes and cemetery transcriptions for the surname in the years after the last documented event.",
            idx, person))
    elif not death["place"].get("raw"):
        findings.append(_finding(
            "missing_death_place", "coverage", "medium",
            "No death place recorded",
            "The death place points to the probate court, the cemetery and the local newspaper.",
            record["raw"][:200],
            "The place of burial is often on the death certificate even when the death place is not.",
            idx, person))

    if "father" not in rels and "mother" not in rels:
        findings.append(_finding(
            "missing_parents", "coverage", "high",
            "No parents named",
            "Without a parent's name this person cannot be matched back to an earlier generation, and their own children cannot be attached.",
            record["raw"][:200],
            "A death certificate, probate petition or Social Security application will name both parents.",
            idx, person))
    elif "father" not in rels or "mother" not in rels:
        findings.append(_finding(
            "one_parent_only", "coverage", "low",
            "Only one parent is named",
            "One parent is enough to search but two makes the match certain.",
            "; ".join(r["raw"] for r in record["relations"]),
            "Look for the second parent in the same record that gave you the first.",
            idx, person))

    if "spouse" not in rels and "marriage" not in types:
        findings.append(_finding(
            "missing_spouse", "coverage", "medium",
            "No spouse or marriage recorded",
            "Marriage records are the densest single source for a family's names and dates.",
            record["raw"][:200],
            "Search the marriage register for the county; the return usually names both sets of parents.",
            idx, person))

    if not (types & {"residence", "migration"}):
        findings.append(_finding(
            "missing_residence", "coverage", "medium",
            "No residence or migration recorded",
            "Residences are how you find this person in a census; without one, every search is a needle in a haystack.",
            record["raw"][:200],
            "Pick one known year and search the census for the surname in the county you already know.",
            idx, person))

    by = date_year(birth["date"]) if birth else None
    dy = date_year(death["date"]) if death else None
    if by and dy and dy - by > 20:
        event_years = [date_year(e["date"]) for e in record["events"] if date_year(e["date"])]
        interior = [y for y in event_years if by < y < dy]
        if not interior:
            findings.append(_finding(
                "lifespan_gap", "coverage", "medium",
                "No evidence for the middle {0} years of the life".format(dy - by),
                "Everything documented sits at the two ends of the life, with nothing in between.",
                "born {0}, died {1}".format(by, dy),
                "A census every ten years should exist - work through the censuses one at a time.",
                idx, person))

    if birth and death:
        bc = birth["place"].get("country")
        dc = death["place"].get("country")
        if bc and dc and bc != dc and "migration" not in types:
            findings.append(_finding(
                "missing_migration", "coverage", "medium",
                "Born in one country, died in another, with no migration recorded",
                "An international move always leaves paperwork - a passenger list, a naturalisation file or a declaration of intention.",
                "born {0} / died {1}".format(bc, dc),
                "Search passenger lists and naturalisation records for the surname in the years between the two events.",
                idx, person))
    return findings


def _check_identity(record, records, findings, pairs):
    person = _label_for(record)
    idx = record["index"]
    first, middle, last = name_parts(record["name_norm"])
    birth = _event_of(record, "birth")
    death = _event_of(record, "death")

    if not record["name_norm"]:
        findings.append(_finding(
            "missing_name", "disambiguation", "high",
            "This claim has no name",
            "A record without a name cannot be searched for, matched or cited.",
            record["raw"][:200],
            "Add the person's name as it appears on the record, then note any variants.",
            idx, person))
        return findings

    if last in COMMON_SURNAMES:
        findings.append(_finding(
            "common_surname", "disambiguation", "medium",
            "'{0}' is too common to identify a person alone".format(last.title()),
            "There may be several people with this surname in the same county, and census and probate indexes will not separate them.",
            record["name_raw"],
            "Pin the person down with a middle name, a birth year, a spouse's name, a street address or an occupation.",
            idx, person))

    if not middle:
        findings.append(_finding(
            "no_middle_name", "disambiguation", "low",
            "No middle name recorded",
            "A middle name is the cheapest way to separate two people with the same name.",
            record["name_raw"],
            "Take the middle name from the birth record, the obituary or the gravestone.",
            idx, person))

    if birth and birth["date"].get("precision") in ("year", "decade", "range"):
        findings.append(_finding(
            "wide_birth_date", "disambiguation", "low",
            "Birth date is only known to the {0}".format(birth["date"]["precision"]),
            "A wide date means every index search has to be widened, which invites the wrong person in.",
            birth["raw"],
            "Narrow it with one more record - a draft card or a probate file gives an exact year.",
            idx, person))

    for pair in pairs:
        if pair["a"] != idx and pair["b"] != idx:
            continue
        other = records[pair["b"] if pair["a"] == idx else pair["a"]]
        other_name = _label_for(other)
        if pair["band"] == "likely_same":
            findings.append(_finding(
                "possible_duplicate", "disambiguation", "high",
                "These two records look like the same person",
                "The names, dates and places line up closely enough that they may be one person recorded twice.",
                "{0} vs {1} - {2}".format(person, other_name, "; ".join(pair["reasons"])),
                "Compare both records line by line before merging them, and keep one as the secondary entry with its own citation.",
                idx, person))
        elif pair["band"] == "possible_same":
            findings.append(_finding(
                "identity_unresolved", "disambiguation", "medium",
                "These two records may or may not be the same person",
                "The evidence is compatible with one person and also with two people who share a name.",
                "{0} vs {1} - {2}".format(person, other_name, "; ".join(pair["reasons"])),
                "Look for a shared relative, a shared street or a shared occupation in one of the two records.",
                idx, person))
        elif pair["conflicts"]:
            findings.append(_finding(
                "same_name_collision", "disambiguation", "medium",
                "Same name, but the dates say these are different people",
                "Merging these two would create a person who never existed.",
                "{0} vs {1} - {2}".format(person, other_name, "; ".join(pair["conflicts"])),
                "Keep them as separate people and label each with the county and the years that separate them.",
                idx, person))
    return findings


def _check_researchability(record, findings):
    person = _label_for(record)
    idx = record["index"]
    birth = _event_of(record, "birth")
    death = _event_of(record, "death")
    by = date_year(birth["date"]) if birth else None
    dy = date_year(death["date"]) if death else None
    place = birth["place"] if birth else (death["place"] if death else {})

    if place.get("country") == "USA" and by and 1888 <= by <= 1891:
        findings.append(_finding(
            "census_gap_1890", "researchability", "medium",
            "The 1890 census is the one that is missing",
            "The 1890 United States census was destroyed by fire and water, so a person of this age cannot be found in it.",
            "born {0}".format(by),
            "Use the 1880 and 1900 censuses instead, and look for a state census in the gap years.",
            idx, person))
    if place.get("country") in ("England", "Wales", "Scotland") and by and 1939 <= by <= 1942:
        findings.append(_finding(
            "census_gap_1941", "researchability", "medium",
            "The 1941 census does not exist",
            "The 1941 British census was destroyed during the war, so this generation has a hole where the 1941 return should be.",
            "born {0}".format(by),
            "Use the 1939 register with the wartime National Registration cards instead.",
            idx, person))

    if by and not suggest_collections("birth", by, place, limit=2):
        findings.append(_finding(
            "no_collection_covers_birth", "researchability", "medium",
            "No obvious record collection covers this birth",
            "Nothing in the standard series covers a birth in this place and year.",
            "born {0} in {1}".format(by, place.get("raw")),
            "Look for local or specialist collections - a state archive, a church archive or a private family history of the county.",
            idx, person))

    for ev in record["events"]:
        if ev["type"] == "migration" and not ev["place"].get("raw"):
            findings.append(_finding(
                "migration_point_unknown", "researchability", "medium",
                "The migration has no arrival point",
                "Without the port or the town of arrival, a passenger list cannot be searched - the lists are filed by ship and port.",
                ev["raw"],
                "Take the year of arrival and the nationality, then search the passenger lists by port and by the least common part of the surname.",
                idx, person))

    if record["sources"] and all(s["type"] in ("interview", "online_tree", "private")
                                 for s in record["sources"] if s["type"]):
        findings.append(_finding(
            "weak_source_types_only", "researchability", "medium",
            "Nothing in this claim can be checked against a record",
            "Every citation is memory, a copy or a private paper - none of which can be confirmed independently.",
            "; ".join(s["raw"] for s in record["sources"][:3]),
            "Convert each one into a search: a name, a place and a decade is enough to start in a register.",
            idx, person))
    return findings


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_sourcing(record):
    sources = record["sources"]
    if not sources:
        return 0
    types = [s["type"] for s in sources if s["type"]]
    if not types:
        return 25
    primary = sum(1 for t in types if SOURCE_TYPES[t]["tier"] == "primary")
    secondary = sum(1 for t in types if SOURCE_TYPES[t]["tier"] == "secondary")
    derivative = sum(1 for t in types if SOURCE_TYPES[t]["tier"] == "derivative")
    undocumented = sum(1 for t in types if SOURCE_TYPES[t]["tier"] == "undocumented")
    score = 0.0
    score += 40 * min(1.0, primary / 3.0)
    score += 18 * min(1.0, secondary / 2.0)
    score += 8 * min(1.0, derivative)
    score += 4 * min(1.0, undocumented)
    distinct = len(set(types))
    score += 25 if distinct >= 4 else (20 if distinct == 3 else (12 if distinct == 2 else 7))
    if any(s.get("year") for s in sources):
        score += 5
    return max(0, min(100, round(score)))


def _score_consistency(record, findings):
    idx = record["index"]
    relevant = [f for f in findings if f["axis"] == "consistency" and f["record_index"] == idx]
    score = 100.0
    for f in relevant:
        if f["severity"] == "high":
            score -= 26
        elif f["severity"] == "medium":
            score -= 11
        else:
            score -= 4
    return max(0, min(100, round(score)))


def _score_coverage(record):
    birth = _event_of(record, "birth")
    death = _event_of(record, "death")
    rels = {r["type"] for r in record["relations"]}
    types = {e["type"] for e in record["events"]}

    total = 0.0
    earned = 0.0
    checks = (
        (2.0, bool(birth and date_is_known(birth["date"]))),
        (1.5, bool(birth and (birth["place"].get("county") or birth["place"].get("state")))),
        (2.0, bool(death and date_is_known(death["date"]))),
        (1.0, bool(death and death["place"].get("precision") not in (None, "unknown"))),
        (1.5, bool(rels & {"father", "mother"})),
        (1.0, bool(rels & {"spouse"}) or "marriage" in types),
        (1.0, bool(types & {"residence", "migration"})),
        (0.5, "occupation" in types),
    )
    for weight, ok in checks:
        total += weight
        if ok:
            earned += weight
    if not total:
        return 0
    return max(0, min(100, round(100 * earned / total)))


def _score_disambiguation(record, findings, pairs):
    idx = record["index"]
    birth = _event_of(record, "birth")
    death = _event_of(record, "death")
    first, middle, last = name_parts(record["name_norm"])
    score = 42.0
    if birth:
        if birth["date"].get("precision") == "day":
            score += 10
        elif date_is_known(birth["date"]):
            score += 6
        if birth["place"].get("county") or birth["place"].get("state"):
            score += 8
    if death and date_is_known(death["date"]):
        score += 8
    types = set(s["type"] for s in record["sources"] if s["type"])
    if len(types) >= 2:
        score += 10
    if middle:
        score += 8
    if record["relations"]:
        score += 8
    if last in COMMON_SURNAMES:
        score -= 14
    if birth and birth["date"].get("ambiguous"):
        score -= 10
    for f in findings:
        if f["axis"] == "disambiguation" and f["record_index"] == idx and f["severity"] == "high":
            score -= 14
    for pair in pairs:
        if pair["a"] == idx or pair["b"] == idx:
            if pair["band"] == "likely_same":
                score -= 10
            elif pair["band"] == "possible_same":
                score -= 6
    return max(0, min(100, round(score)))


def _score_researchability(record):
    place = {}
    birth = _event_of(record, "birth")
    death = _event_of(record, "death")
    if birth:
        place = birth["place"]
    elif death:
        place = death["place"]
    by = date_year(birth["date"]) if birth else None
    dy = date_year(death["date"]) if death else None

    score = 0.0
    if place.get("county") or place.get("state"):
        score += 15
    if place.get("country"):
        score += 5

    targets = census_targets(by, dy, place) if by else []
    score += 20 if targets else 5

    resolvable = 0
    unresolved = 0
    if not birth or not date_is_known(birth["date"]):
        unresolved += 1
        if suggest_collections("birth", by, place, limit=1):
            resolvable += 1
    if not birth or not birth["place"].get("raw"):
        unresolved += 1
    if not death or not date_is_known(death["date"]):
        unresolved += 1
        if suggest_collections("death", dy, place, limit=1):
            resolvable += 1
    if unresolved:
        score += 30 * (resolvable / float(unresolved))
    else:
        score += 30
    if any(s["type"] == "census" for s in record["sources"]):
        score += 5
    return max(0, min(100, round(score)))


# ---------------------------------------------------------------------------
# Research plan
# ---------------------------------------------------------------------------

def _census_collection_for(country):
    mapping = {
        "USA": "us-census",
        "England": "uk-census",
        "Wales": "uk-census",
        "Scotland": "uk-census",
        "Ireland": "ireland",
        "Canada": "canada-census",
        "Australia": "australia-census",
    }
    wanted = mapping.get(country)
    for c in COLLECTIONS:
        if wanted and c["id"] == wanted:
            return c
    if not country:
        for c in COLLECTIONS:
            if c.get("kind") == "census":
                return c
    return None


def _build_plan(records, findings, pairs):
    plan = []
    name_counts = {}
    for rec in records:
        key = rec.get("name_norm")
        if key:
            name_counts[key] = name_counts.get(key, 0) + 1
    linked = {}
    for rec in records:
        key = rec.get("name_norm")
        if key:
            linked[key] = linked.get(key, 0) + 1
    for other in records:
        for rel in other["relations"]:
            key = rel.get("name_norm")
            if key:
                linked[key] = linked.get(key, 0) + 1

    def add(item):
        item["priority_score"] = round(item["impact"] * item["likelihood"], 2)
        plan.append(item)

    for rec in records:
        person = _label_for(rec)
        if name_counts.get(rec.get("name_norm"), 0) > 1:
            person = "{0} (claim {1})".format(person, rec["index"] + 1)
        idx = rec["index"]
        before = len(plan)
        birth = _event_of(rec, "birth")
        death = _event_of(rec, "death")
        by = date_year(birth["date"]) if birth else None
        dy = date_year(death["date"]) if death else None
        place = birth["place"] if birth else (death["place"] if death else {})
        unlocks = max(0, linked.get(rec.get("name_norm"), 1) - 1)

        for f in findings:
            if f["record_index"] != idx:
                continue
            if f["id"] not in ("conflicting_dates", "conflicting_places",
                               "marriage_before_birth", "marriage_after_death",
                               "birth_after_death", "source_predates_event",
                               "possible_duplicate", "identity_unresolved"):
                continue
            wanted = "birth" if "birth" in f["id"] else "death"
            cols = suggest_collections(wanted, by or dy, place, limit=3)
            col = cols[0] if cols else None
            add({
                "kind": "conflict",
                "title": "Resolve: {0}".format(f["title"]),
                "detail": "{0} For {1}.".format(f["detail"], person),
                "why": f["fix"],
                "collection_id": col["id"] if col else None,
                "collection_name": col["name"] if col else "Search the county record set directly",
                "target_year": (by or dy),
                "look_for": col["how_to"] if col else "Work the county indexes for the surname across a five-year window.",
                "impact": 3 + min(3, unlocks),
                "likelihood": 0.8 if col else 0.4,
                "person": person,
            })

        milestones = []
        if not birth or not date_is_known(birth["date"]):
            milestones.append(("birth", by, place))
        if not death or not date_is_known(death["date"]):
            milestones.append(("death", dy, place))
        if not any(r["type"] in ("father", "mother") for r in rec["relations"]):
            milestones.append(("parent", by, place))
        for mtype, year, p in milestones:
            cols = suggest_collections(mtype, year, p, limit=3)
            col = cols[0] if cols else None
            add({
                "kind": "gap",
                "title": "Establish the {0} of {1}".format(mtype, person),
                "detail": "No {0} is currently attached to this claim.".format(mtype),
                "why": "Every collection that can answer this is listed below - start with the highest specificity.",
                "collection_id": col["id"] if col else None,
                "collection_name": col["name"] if col else "County record set for the place",
                "target_year": year,
                "look_for": col["how_to"] if col else "Search the county's vital register, probate and parish indexes.",
                "impact": 2 + min(3, unlocks),
                "likelihood": 0.75 if col else 0.35,
                "person": person,
            })

        targets = census_targets(by, dy, place) if by else []
        if targets:
            listing = ", ".join("{0} (age {1})".format(y, a) for y, a in targets[:5])
            col = _census_collection_for(place.get("country"))
            add({
                "kind": "census",
                "title": "Pull the census records for {0}".format(person),
                "detail": "Census years worth checking: {0}.".format(listing),
                "why": "Census records are the cheapest way to find the household, the occupation and the places the family moved through.",
                "collection_id": col["id"] if col else None,
                "collection_name": col["name"] if col else "National census series",
                "target_year": targets[0][0] if targets else None,
                "look_for": col["how_to"] if col else "Search by surname in the county, then walk the neighbouring households.",
                "impact": 2 + min(2, unlocks),
                "likelihood": 0.85,
                "person": person,
            })

        weak = [s for s in rec["sources"]
                if s["type"] in DERIVATIVE_TYPES or s["type"] == "interview"]
        if weak and len(rec["sources"]) <= len(weak) + 1:
            is_tree = weak[0]["type"] in DERIVATIVE_TYPES
            add({
                "kind": "upgrade",
                "title": ("Replace the copied evidence for {0}" if is_tree
                          else "Turn the family memory into a search for {0}").format(person),
                "detail": "The only real citation is {0}: {1}".format(
                    "a copy" if is_tree else "a recollection", weak[0]["raw"]),
                "why": ("A tree copies errors as faithfully as it copies facts. Trace it back to the original record and cite that instead."
                        if is_tree else
                        "Memory is a good lead and a poor proof. Take the name, the place and the decade to a register or a census."),
                "collection_id": None,
                "collection_name": ("The record the tree itself points to" if is_tree
                                    else "The parish register, census or civil register for the place"),
                "target_year": by,
                "look_for": ("Open the tree's citation, find the record behind it, then re-cite the record."
                             if is_tree else
                             "Search every register for the surname in a five-year window around the claimed date."),
                "impact": 2,
                "likelihood": 0.6,
                "person": person,
            })

        if len(plan) == before:
            cols = suggest_collections("birth", by, place, limit=2)
            col = cols[0] if cols else None
            add({
                "kind": "review",
                "title": "Widen the evidence for {0}".format(person),
                "detail": "Nothing on this claim is missing a date or a place, so the next task is corroboration rather than repair.",
                "why": "Add a second, independent record of a different type so the claim can be tested against something.",
                "collection_id": col["id"] if col else None,
                "collection_name": (col["name"] if col else
                                    "Civil register of {0}".format(place.get("country") or "the birth place")),
                "target_year": by,
                "look_for": (col["how_to"] if col else
                             "Search the local civil register for the birth year and the parish, then work forward."),
                "impact": 1,
                "likelihood": 0.5,
                "person": person,
            })

    for pair in pairs:
        if pair["band"] == "likely_same":
            a = _label_for(records[pair["a"]])
            b = _label_for(records[pair["b"]])
            add({
                "kind": "identity",
                "title": "Decide whether {0} and {1} are the same person".format(a, b),
                "detail": "Score {0}/100. {1}".format(pair["score"], "; ".join(pair["reasons"])),
                "why": "Merging two records that are actually two people creates an ancestor who never lived; splitting one person into two loses a generation.",
                "collection_id": None,
                "collection_name": "The census, probate or vital record where the two records overlap",
                "target_year": None,
                "look_for": "Compare parents, spouse, occupation, street and neighbours in the overlapping census year.",
                "impact": 3,
                "likelihood": 0.7,
                "person": "{0} / {1}".format(a, b),
            })

    plan.sort(key=lambda x: -x["priority_score"])
    for i, item in enumerate(plan):
        item["rank"] = i + 1
    return plan[:30]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze(text, mode="standard"):
    mode_key = mode if mode in MODES else "standard"
    mode_def = MODES[mode_key]
    records = parse_notes(text)

    findings = []
    for rec in records:
        _check_timeline(rec, findings)
    for rec in records:
        _check_sources(rec, findings)
    for rec in records:
        _check_coverage(rec, findings)
    pairs = identity_pairs(records)
    for rec in records:
        _check_identity(rec, records, findings, pairs)
    for rec in records:
        _check_researchability(rec, findings)

    if not records:
        findings.append(_finding(
            "empty_input", "sourcing", "high",
            "Nothing to analyse",
            "No person claims could be read from the notes.",
            (text or "")[:200],
            "Start each person with their name on its own line, then add Born:, Died:, Father:, Source: lines beneath.",
            None, ""))

    per_record = []
    for rec in records:
        rec_scores = {
            "sourcing": _score_sourcing(rec),
            "consistency": _score_consistency(rec, findings),
            "coverage": _score_coverage(rec),
            "disambiguation": _score_disambiguation(rec, findings, pairs),
            "researchability": _score_researchability(rec),
        }
        per_record.append({
            "index": rec["index"],
            "name": _label_for(rec),
            "scores": rec_scores,
            "overall": round(sum(rec_scores[a] * mode_def["weights"][a] for a in AXES), 1),
        })

    axis_scores = {}
    for axis in AXES:
        if per_record:
            axis_scores[axis] = round(
                sum(r["scores"][axis] for r in per_record) / float(len(per_record)), 1)
        else:
            axis_scores[axis] = 0.0

    overall = round(sum(axis_scores[a] * mode_def["weights"][a] for a in AXES), 1)

    caps = []
    high_findings = [f for f in findings if f["severity"] == "high"]
    unsourced = [f for f in findings if f["id"] == "no_sources"]
    hard_conflicts = [f for f in findings if f["id"] in (
        "conflicting_dates", "birth_after_death", "marriage_before_birth",
        "marriage_after_death", "possible_duplicate", "source_predates_event")]
    unsupported_share = 0.0
    if records:
        unsupported = sum(1 for rec in records if not rec["sources"])
        unsupported_share = unsupported / float(len(records))

    if unsourced:
        axis_scores["sourcing"] = min(axis_scores["sourcing"], 20)
        overall = min(overall, 45)
        caps.append("At least one person has no citation at all, so the overall score is capped at 45.")
    if hard_conflicts:
        axis_scores["consistency"] = min(axis_scores["consistency"], 45)
        caps.append("An unresolved conflict is present, so consistency is capped at 45.")
    if len(high_findings) >= 4:
        overall = min(overall, 60)
        caps.append("{0} high-severity findings are open, so the overall score is capped at 60.".format(len(high_findings)))
    if unsupported_share >= 0.4 and records:
        overall = min(overall, 55)
        caps.append("{0}% of the people have no source, so the overall score is capped at 55.".format(
            round(unsupported_share * 100)))
    if axis_scores["coverage"] < 35:
        overall = min(overall, 55)
        caps.append("Coverage of the life is below 35, so the overall score is capped at 55.")

    overall = round(overall, 1)
    axis_scores = {k: round(v, 1) for k, v in axis_scores.items()}

    verdict = "speculation"
    for threshold, key, _desc in VERDICT_BANDS:
        if overall >= threshold:
            verdict = key
            break

    plan = _build_plan(records, findings, pairs)

    strengths = []
    if records:
        if all(rec["sources"] for rec in records):
            strengths.append("Every person on this list has at least one citation attached.")
        primary = sum(1 for rec in records for s in rec["sources"]
                      if s["type"] and SOURCE_TYPES[s["type"]]["tier"] == "primary")
        if primary:
            strengths.append("{0} primary record{1} cited - these carry the most weight.".format(
                primary, "" if primary == 1 else "s"))
        dated_births = sum(1 for rec in records
                           for ev in rec["events"]
                           if ev["type"] == "birth" and date_is_known(ev["date"]))
        if dated_births:
            strengths.append("{0} birth date{1} placed on the timeline.".format(
                dated_births, "" if dated_births == 1 else "s"))
        with_places = sum(1 for rec in records
                          for ev in rec["events"]
                          if ev["type"] in ("birth", "death") and ev["place"].get("county"))
        if with_places:
            strengths.append("{0} event{1} carry a county, which is enough to search a register.".format(
                with_places, "" if with_places == 1 else "s"))
        if plan:
            strengths.append("Every open question has a named collection to go to next.")

    by_axis = {a: {"high": 0, "medium": 0, "low": 0, "total": 0} for a in AXES}
    by_severity = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        if f["axis"] not in by_axis:
            by_axis[f["axis"]] = {"high": 0, "medium": 0, "low": 0, "total": 0}
        by_axis[f["axis"]]["total"] += 1
        by_axis[f["axis"]][f["severity"]] += 1
        by_severity[f["severity"]] += 1

    result = {
        "mode": mode_key,
        "mode_label": mode_def["label"],
        "weights": mode_def["weights"],
        "overall": overall,
        "verdict": verdict,
        "verdict_label": next(d for t, k, d in VERDICT_BANDS if k == verdict),
        "axes": [
            {"key": a, "label": AXIS_LABELS[a], "description": AXIS_DESCRIPTIONS[a],
             "score": axis_scores[a], "weight": mode_def["weights"][a]}
            for a in AXES
        ],
        "caps": caps,
        "records": [_public_record(rec) for rec in records],
        "per_record": per_record,
        "findings": findings,
        "findings_by_axis": by_axis,
        "findings_by_severity": by_severity,
        "identity": [
            {"a": p["a"], "b": p["b"], "score": p["score"], "band": p["band"],
             "reasons": p["reasons"], "conflicts": p["conflicts"],
             "a_name": _label_for(records[p["a"]]),
             "b_name": _label_for(records[p["b"]])}
            for p in pairs
        ],
        "plan": plan,
        "strengths": strengths,
        "collections": [
            {"id": c["id"], "name": c["name"], "country": c.get("country"),
             "kind": c.get("kind"), "year_note": c.get("year_note"),
             "proves": c.get("proves"), "how_to": c.get("how_to"),
             "caveats": c.get("caveats", [])}
            for c in COLLECTIONS
        ],
        "counts": {
            "records": len(records),
            "events": sum(len(r["events"]) for r in records),
            "sources": sum(len(r["sources"]) for r in records),
            "findings": len(findings),
            "plan_items": len(plan),
        },
    }
    return result


def _public_record(rec):
    return {
        "index": rec["index"],
        "name": _label_for(rec),
        "name_raw": rec["name_raw"],
        "name_norm": rec["name_norm"],
        "events": [
            {
                "type": ev["type"],
                "raw": ev["raw"],
                "date": ev["date"],
                "date_display": format_date(ev["date"]),
                "place": ev["place"],
                "place_display": format_place(ev["place"]),
                "sources": ev.get("sources", []),
            }
            for ev in rec["events"]
        ],
        "relations": rec["relations"],
        "sources": rec["sources"],
        "notes": rec["notes"],
        "conflicts": rec["conflicts"],
        "raw": rec["raw"],
    }


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

def to_markdown(result):
    lines = []
    lines.append("# Family History Evidence Worksheet")
    lines.append("")
    lines.append("**Evidence readiness: {0}/100 - {1}**".format(
        result["overall"], result["verdict"].replace("_", " ")))
    lines.append("")
    lines.append("Mode: {0}".format(result["mode_label"]))
    lines.append("")
    lines.append("## Scorecard")
    lines.append("")
    lines.append("| Axis | Score | Weight | What it measures |")
    lines.append("| --- | --- | --- | --- |")
    for ax in result["axes"]:
        lines.append("| {0} | {1} | {2:.0%} | {3} |".format(
            ax["label"], ax["score"], ax["weight"], ax["description"]))
    lines.append("")
    if result["caps"]:
        lines.append("### Structural caps applied")
        lines.append("")
        for cap in result["caps"]:
            lines.append("- {0}".format(cap))
        lines.append("")
    if result["strengths"]:
        lines.append("### Strengths")
        lines.append("")
        for s in result["strengths"]:
            lines.append("- {0}".format(s))
        lines.append("")

    lines.append("## People")
    lines.append("")
    for rec in result["records"]:
        lines.append("### {0}".format(rec["name"]))
        lines.append("")
        if rec["events"]:
            lines.append("| Event | Date | Place | Evidence |")
            lines.append("| --- | --- | --- | --- |")
            for ev in rec["events"]:
                srcs = "; ".join(ev["sources"]) if ev["sources"] else "-"
                lines.append("| {0} | {1} | {2} | {3} |".format(
                    ev["type"], ev["date_display"], ev["place_display"], srcs))
            lines.append("")
        else:
            lines.append("_No dated events._")
            lines.append("")
        if rec["relations"]:
            lines.append("Relations: " + "; ".join(
                "{0} {1}".format(r["type"], r["raw"]) for r in rec["relations"]))
            lines.append("")
        if rec["sources"]:
            lines.append("Sources:")
            for src in rec["sources"]:
                lines.append("- {0} - {1} ({2})".format(src["raw"], src["label"], src["tier"]))
            lines.append("")
        if rec["notes"]:
            lines.append("Notes: " + "; ".join(rec["notes"]))
            lines.append("")

    lines.append("## Findings")
    lines.append("")
    if not result["findings"]:
        lines.append("No findings.")
    else:
        order = {"high": 0, "medium": 1, "low": 2}
        for f in sorted(result["findings"], key=lambda x: (order[x["severity"]], x["axis"], x["id"])):
            lines.append("### [{0}] {1} ({2})".format(
                f["severity"].upper(), f["title"], f["person"] or "-"))
            lines.append("")
            lines.append(f["detail"])
            lines.append("")
            if f["evidence"]:
                lines.append("**Evidence:** {0}".format(f["evidence"].replace("\n", " ")))
                lines.append("")
            lines.append("**Fix:** {0}".format(f["fix"]))
            lines.append("")

    lines.append("## Research plan")
    lines.append("")
    if not result["plan"]:
        lines.append("Nothing outstanding - or nothing to search yet.")
    else:
        lines.append("| # | Action | Collection | Why |")
        lines.append("| --- | --- | --- | --- |")
        for item in result["plan"]:
            lines.append("| {0} | {1} | {2} | {3} |".format(
                item["rank"], item["title"], item["collection_name"], item["why"]))
        lines.append("")
        lines.append("### How to search each collection")
        lines.append("")
        for item in result["plan"][:8]:
            if not item.get("look_for"):
                continue
            lines.append("- **{0}** - {1}".format(item["collection_name"], item["look_for"]))
        lines.append("")

    lines.append("## Record collections reference")
    lines.append("")
    for c in result["collections"]:
        lines.append("### {0}".format(c["name"]))
        lines.append("")
        lines.append("- Coverage: {0}".format(c["year_note"]))
        lines.append("- Proves: {0}".format(c["proves"]))
        lines.append("- How: {0}".format(c["how_to"]))
        for cav in c["caveats"]:
            lines.append("- Caution: {0}".format(cav))
        lines.append("")
    return "\n".join(lines)


def to_csv(result):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["person", "event_type", "date", "date_precision", "place",
                     "country", "state", "county", "city", "sources", "notes"])
    for rec in result["records"]:
        if not rec["events"]:
            writer.writerow([rec["name"], "", "", "", "", "", "", "", "",
                             "", "; ".join(rec["notes"])])
            continue
        for ev in rec["events"]:
            writer.writerow([
                rec["name"], ev["type"], ev["date_display"], ev["date"].get("precision", ""),
                ev["place_display"], ev["place"].get("country") or "",
                ev["place"].get("state") or "", ev["place"].get("county") or "",
                ev["place"].get("city") or "",
                "; ".join(ev["sources"]) or "; ".join(s["raw"] for s in rec["sources"]),
                "; ".join(rec["notes"]),
            ])
    return buf.getvalue()


def to_json(result):
    return json.dumps(result, indent=2, sort_keys=False)
