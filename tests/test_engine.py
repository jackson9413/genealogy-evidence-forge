"""Smoke tests for genealogy-evidence-forge.

Run with:  python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gh_analysis import analyze, to_csv, to_json, to_markdown  # noqa: E402
from gh_parser import (  # noqa: E402
    detect_source_type,
    normalize_name,
    parse_date,
    parse_notes,
    parse_place,
    split_records,
)
from gh_sources import COLLECTIONS, SOURCE_TYPES  # noqa: E402

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def read_sample(name):
    with open(os.path.join(SAMPLE_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


class TestDateParsing(unittest.TestCase):
    def test_day_month_year(self):
        d = parse_date("12 March 1872")
        self.assertEqual((d["day"], d["month"], d["year"]), (12, 3, 1872))
        self.assertEqual(d["precision"], "day")

    def test_month_day_year(self):
        d = parse_date("March 12, 1872")
        self.assertEqual((d["day"], d["month"], d["year"]), (12, 3, 1872))

    def test_numeric_ambiguous(self):
        d = parse_date("03/12/1876")
        self.assertTrue(d["ambiguous"])
        self.assertEqual(d["month"], 3)

    def test_numeric_unambiguous_day_first(self):
        d = parse_date("25/12/1876")
        self.assertEqual((d["day"], d["month"]), (25, 12))
        self.assertFalse(d["ambiguous"])

    def test_year_only(self):
        d = parse_date("1872")
        self.assertEqual(d["year"], 1872)
        self.assertEqual(d["precision"], "year")

    def test_circa(self):
        d = parse_date("c. 1872")
        self.assertTrue(d["circa"])
        self.assertEqual(d["year"], 1872)

    def test_before(self):
        d = parse_date("before 1875")
        self.assertEqual(d["qualifier"], "before")
        self.assertEqual(d["year"], 1875)

    def test_between(self):
        d = parse_date("between 1860 and 1870")
        self.assertEqual(d["precision"], "range")
        self.assertEqual((d["year_start"], d["year_end"]), (1860, 1870))

    def test_decade(self):
        d = parse_date("1940s")
        self.assertEqual(d["precision"], "decade")
        self.assertEqual(d["year"], 1940)

    def test_age(self):
        d = parse_date("aged 21")
        self.assertEqual(d["precision"], "age")

    def test_iso(self):
        d = parse_date("1872-03-12")
        self.assertEqual((d["year"], d["month"], d["day"]), (1872, 3, 12))

    def test_no_date(self):
        self.assertEqual(parse_date("somewhere in the old country")["precision"], "unknown")


class TestPlaceParsing(unittest.TestCase):
    def test_us_place(self):
        p = parse_place("Warren County, Ohio")
        self.assertEqual(p["country"], "USA")
        self.assertEqual(p["state_abbr"], "OH")
        self.assertEqual(p["county"], "Warren County")

    def test_uk_place(self):
        p = parse_place("Keighley, Yorkshire, England")
        self.assertEqual(p["country"], "England")
        self.assertEqual(p["county"], "Yorkshire")

    def test_noise_place_is_rejected(self):
        p = parse_place("not sure, maybe in the 1940s?")
        self.assertEqual(p["precision"], "unknown")
        self.assertEqual(p["raw"], "")

    def test_country_only(self):
        p = parse_place("Ireland")
        self.assertEqual(p["country"], "Ireland")
        self.assertEqual(p["precision"], "country")


class TestSourceDetection(unittest.TestCase):
    def test_census_beats_township(self):
        kind, _ = detect_source_type("1880 US Census, Tarlton Township, Warren County, Ohio")
        self.assertEqual(kind, "census")

    def test_death_certificate(self):
        kind, _ = detect_source_type("Death certificate, Ohio Department of Health, 1943")
        self.assertEqual(kind, "civil_death")

    def test_probate(self):
        kind, _ = detect_source_type("Will and probate, Warren County Probate Court, 1944")
        self.assertEqual(kind, "probate")

    def test_gravestone(self):
        kind, _ = detect_source_type("Gravestone photograph, Union Cemetery, Ohio")
        self.assertEqual(kind, "grave")

    def test_online_tree(self):
        kind, _ = detect_source_type("Ancestry.com public tree")
        self.assertEqual(kind, "online_tree")

    def test_oral_tradition(self):
        kind, _ = detect_source_type("My aunt Mary said he was born in Ireland")
        self.assertEqual(kind, "interview")

    def test_passenger_list(self):
        kind, _ = detect_source_type("Passenger list, Bremen to New York, 1892")
        self.assertEqual(kind, "immigration")

    def test_1939_register(self):
        kind, _ = detect_source_type("1939 Register, Leeds, Yorkshire")
        self.assertEqual(kind, "population_register")

    def test_every_source_type_has_tier(self):
        for key, profile in SOURCE_TYPES.items():
            self.assertIn(profile["tier"], ("primary", "secondary", "derivative", "undocumented"))
            self.assertTrue(profile["keywords"])


class TestNames(unittest.TestCase):
    def test_titles_removed(self):
        self.assertEqual(normalize_name("Mr John Albert Smith"), "john albert smith")

    def test_suffix_removed(self):
        self.assertEqual(normalize_name("John Smith Jr."), "john smith")

    def test_parenthetical_removed(self):
        self.assertEqual(normalize_name("John Smith (b. 1872)"), "john smith")


class TestRecordParsing(unittest.TestCase):
    def test_split_on_blank_line(self):
        blocks = split_records("A\nBorn: 1800\n\nB\nBorn: 1900")
        self.assertEqual(len(blocks), 2)

    def test_split_on_rule(self):
        blocks = split_records("A\nBorn: 1800\n---\nB\nBorn: 1900")
        self.assertEqual(len(blocks), 2)

    def test_event_date_and_place_split(self):
        recs = parse_notes("John Smith\nBorn: 12 March 1872, Warren County, Ohio")
        ev = recs[0]["events"][0]
        self.assertEqual(ev["date"]["year"], 1872)
        self.assertEqual(ev["place"]["state_abbr"], "OH")

    def test_relations_parsed(self):
        recs = parse_notes("John Smith\nFather: Samuel Smith (born 1841)")
        self.assertEqual(recs[0]["relations"][0]["type"], "father")

    def test_sources_parsed(self):
        recs = parse_notes("John Smith\nSources: 1880 US Census, Ohio; 1900 US Census, Ohio")
        self.assertEqual(len(recs[0]["sources"]), 2)


class TestAnalysis(unittest.TestCase):
    def test_empty_input(self):
        r = analyze("")
        self.assertEqual(r["counts"]["records"], 0)
        self.assertEqual(r["overall"], 0)
        self.assertTrue(any(f["id"] == "empty_input" for f in r["findings"]))

    def test_overall_in_range(self):
        for name in os.listdir(SAMPLE_DIR):
            if not name.endswith(".md"):
                continue
            r = analyze(read_sample(name))
            self.assertGreaterEqual(r["overall"], 0)
            self.assertLessEqual(r["overall"], 100)
            for ax in r["axes"]:
                self.assertGreaterEqual(ax["score"], 0)
                self.assertLessEqual(ax["score"], 100)

    def test_verdict_bands(self):
        r = analyze(read_sample("ohio_ancestor.md"))
        self.assertIn(r["verdict"], ("research_ready", "solid_progress", "needs_work",
                                     "thin_evidence", "speculation"))

    def test_unsourced_person_is_capped(self):
        r = analyze("Grandpa Jim\nBorn: around 1870")
        self.assertTrue(any(f["id"] == "no_sources" for f in r["findings"]))
        self.assertLessEqual(r["overall"], 45)
        self.assertTrue(r["caps"])

    def test_conflicting_dates_detected(self):
        r = analyze(
            "John Smith\n"
            "Born: 1870, Warren County, Ohio\n"
            "Died: 1941, Warren County, Ohio\n"
            "Died: 1943, Warren County, Ohio\n"
            "Source: 1900 US Census, Warren County, Ohio\n")
        self.assertTrue(any(f["id"] == "conflicting_dates" for f in r["findings"]))

    def test_birth_after_death(self):
        r = analyze(
            "John Smith\n"
            "Born: 1950, Warren County, Ohio\n"
            "Died: 1940, Warren County, Ohio\n"
            "Source: 1900 US Census, Warren County, Ohio\n")
        self.assertTrue(any(f["id"] == "birth_after_death" for f in r["findings"]))

    def test_same_name_collision(self):
        r = analyze(read_sample("same_name.md"))
        self.assertTrue(r["identity"])
        self.assertEqual(r["identity"][0]["band"], "probably_different")
        self.assertTrue(any(f["id"] == "same_name_collision" for f in r["findings"]))

    def test_census_gap_1890(self):
        r = analyze(
            "John Smith\n"
            "Born: 1890, Warren County, Ohio\n"
            "Died: 1950, Warren County, Ohio\n"
            "Source: 1900 US Census, Warren County, Ohio\n")
        self.assertTrue(any(f["id"] == "census_gap_1890" for f in r["findings"]))

    def test_census_gap_1941(self):
        r = analyze(read_sample("yorkshire.md"))
        self.assertTrue(any(f["id"] == "census_gap_1941" for f in r["findings"]))

    def test_online_tree_flagged(self):
        r = analyze(
            "John Smith\n"
            "Born: 1870, Warren County, Ohio\n"
            "Died: 1940, Warren County, Ohio\n"
            "Source: Ancestry.com public tree\n")
        self.assertTrue(any(f["id"] == "online_tree_citation" for f in r["findings"]))
        self.assertTrue(any(f["id"] == "derivative_sources_only" for f in r["findings"]))

    def test_plan_is_populated(self):
        r = analyze(read_sample("family_story.md"))
        self.assertTrue(r["plan"])
        self.assertIn("collection_name", r["plan"][0])

    def test_plan_names_real_collections(self):
        ids = set(c["id"] for c in COLLECTIONS)
        r = analyze(read_sample("yorkshire.md"))
        for item in r["plan"]:
            if item.get("collection_id"):
                self.assertIn(item["collection_id"], ids)

    def test_census_targets_for_us(self):
        r = analyze(read_sample("ohio_ancestor.md"))
        titles = [p["title"] for p in r["plan"]]
        self.assertTrue(any("census" in t.lower() for t in titles))

    def test_modes_change_weights(self):
        standard = analyze(read_sample("same_name.md"), mode="standard")
        dna = analyze(read_sample("same_name.md"), mode="dna")
        self.assertNotEqual(standard["weights"], dna["weights"])
        self.assertEqual(dna["mode"], "dna")

    def test_unknown_mode_falls_back(self):
        r = analyze("John Smith", mode="nonsense")
        self.assertEqual(r["mode"], "standard")

    def test_every_finding_has_fix(self):
        r = analyze(read_sample("conflicts.md"))
        for f in r["findings"]:
            self.assertTrue(f["fix"])
            self.assertTrue(f["detail"])
            self.assertIn(f["severity"], ("high", "medium", "low"))
            self.assertIn(f["axis"], ("sourcing", "consistency", "coverage",
                                      "disambiguation", "researchability"))


class TestExports(unittest.TestCase):
    def setUp(self):
        self.result = analyze(read_sample("ohio_ancestor.md"))

    def test_markdown(self):
        md = to_markdown(self.result)
        self.assertIn("# Family History Evidence Worksheet", md)
        self.assertIn("## Scorecard", md)
        self.assertIn("## Research plan", md)
        self.assertIn("John Albert Smith", md)

    def test_csv(self):
        csv = to_csv(self.result)
        lines = csv.strip().splitlines()
        self.assertTrue(lines[0].startswith("person,event_type"))
        self.assertGreater(len(lines), 2)

    def test_json(self):
        data = json.loads(to_json(self.result))
        self.assertEqual(data["overall"], self.result["overall"])
        self.assertIn("axes", data)


if __name__ == "__main__":
    unittest.main()
