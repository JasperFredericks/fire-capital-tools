"""
Unit tests for the Investor Report notetaker.

The parts worth pinning are the ones where being wrong is invisible:

  * a match that picks the wrong property puts one building's operations
    into another building's investor update, and reads perfectly;
  * a claim attributed to a meeting that was not in the query is a
    fabricated citation, which is worse than no citation;
  * a cache key that ignores a newly uploaded transcript serves a stale
    document as if it were current.
"""

import tempfile
import unittest
from pathlib import Path

from tools import investor_notes_db as notes_db
from tools import investor_notes_match as matching
from tools import investor_notes_properties as properties
from tools import investor_notes_synth as synth
from tools import upload_limits as ul

JACKSON = {"key": "deal:1", "label": "1120 Jackson Street, San Francisco CA",
           "address": "1120 Jackson Street", "aliases": []}
BAY_VISTA = {"key": "deal:2", "label": "19 Bay Vista Drive, Mill Valley CA",
             "address": "19 Bay Vista Drive", "aliases": []}
EAGLE = {"key": "label:eagle rock apartments", "label": "Eagle Rock Apartments",
         "address": None, "aliases": []}
ALL = [JACKSON, BAY_VISTA, EAGLE]


class ShortFormTests(unittest.TestCase):
    """Nobody says the full address out loud, which is what made the
    first version of this match nothing."""

    def test_a_street_address_reduces_to_its_street_name(self):
        self.assertEqual(
            matching.derive_short_form("1120 Jackson Street",
                                       matching.STREET_SUFFIXES), "jackson")

    def test_a_two_word_street_survives(self):
        self.assertEqual(
            matching.derive_short_form("19 Bay Vista Drive",
                                       matching.STREET_SUFFIXES), "bay vista")

    def test_a_property_name_drops_its_type_word(self):
        self.assertEqual(
            matching.derive_short_form("Eagle Rock Apartments",
                                       matching.PROPERTY_SUFFIXES), "eagle rock")

    def test_a_short_result_is_refused(self):
        """'Elm Street' -> 'elm' is too short to be distinctive, and a
        three-letter token matches ordinary speech."""
        self.assertIsNone(
            matching.derive_short_form("12 Elm Street", matching.STREET_SUFFIXES))

    def test_nothing_removed_means_no_short_form(self):
        self.assertIsNone(
            matching.derive_short_form("Eagle Rock", matching.PROPERTY_SUFFIXES))

    def test_phrases_include_both_the_record_and_what_is_said(self):
        phrases = [matching.normalize(p) for p in matching.phrases_for(JACKSON)]
        self.assertIn("jackson", phrases)
        self.assertIn("1120 jackson street", phrases)


class MatchTests(unittest.TestCase):
    def test_the_property_actually_discussed_wins(self):
        body = ("Ops sync. Jackson occupancy is 94. Two turns at Jackson. "
                "Jackson lobby repaint done. Jackson arrears cleared.")
        result = matching.match(body, ALL)
        self.assertEqual(result["outcome"], "matched")
        self.assertEqual(result["key"], "deal:1")

    def test_a_passing_mention_first_does_not_win(self):
        """First-hit matching would pick Eagle Rock here."""
        body = ("Quick note on Eagle Rock, then Jackson. Jackson occupancy up. "
                "Jackson turns done. Jackson repaint complete. Jackson leasing.")
        self.assertEqual(matching.match(body, ALL)["key"], "deal:1")

    def test_two_properties_discussed_is_ambiguous_not_a_guess(self):
        body = ("Jackson and Bay Vista both need sign-off. Jackson is fine. "
                "Bay Vista needs a roof. Jackson arrears cleared. "
                "Bay Vista vacancy remains.")
        result = matching.match(body, ALL)
        self.assertEqual(result["outcome"], "ambiguous")
        self.assertIsNone(result["key"])

    def test_nothing_mentioned_is_unassigned(self):
        result = matching.match("Payroll moves to the 15th. Insurance renewal.",
                                ALL)
        self.assertEqual(result["outcome"], "unassigned")
        self.assertIsNone(result["key"])

    def test_a_single_passing_mention_is_not_a_match(self):
        self.assertEqual(
            matching.match("Someone mentioned Jackson once.", ALL)["outcome"],
            "unassigned")

    def test_the_evidence_is_always_returned(self):
        result = matching.match("Jackson Jackson Jackson Jackson.", ALL)
        self.assertTrue(result["reason"])
        self.assertTrue(result["candidates"])
        top = result["candidates"][0]
        self.assertTrue(top["phrases"])
        self.assertEqual(top["phrases"][0]["phrase"].lower(), "jackson")

    def test_repeated_spellings_do_not_inflate_a_score(self):
        """'1120 Jackson Street' matches the full address, the street line
        and the derived name. That is one mention, not three."""
        ranked = matching.score("1120 Jackson Street", ALL)
        self.assertEqual(ranked[0]["mentions"], 1)

    def test_aliases_add_to_the_count(self):
        with_alias = dict(EAGLE, aliases=["The Rock"])
        body = "The Rock is leasing well. Eagle Rock roof work starts Monday."
        plain = matching.score(body, [EAGLE])[0]["mentions"]
        aliased = matching.score(body, [with_alias])[0]["mentions"]
        self.assertGreater(aliased, plain)

    def test_a_word_boundary_is_respected(self):
        self.assertEqual(matching.count_mentions(
            matching.normalize("jacksonville is elsewhere"), "jackson"), 0)


class PropertyRegistryTests(unittest.TestCase):
    DEALS = [{"id": 1, "address": "1120 Jackson Street", "city": "San Francisco",
              "state": "CA"}]

    def test_all_three_sources_contribute(self):
        entries = properties.build(self.DEALS, ["Eagle Rock Apartments"],
                                   ["19 bay vista drive"])
        labels = [e["label"] for e in entries]
        self.assertEqual(len(entries), 3, labels)

    def test_a_label_matching_a_deal_is_folded_in_not_duplicated(self):
        """Site DD's free-text label and Deal Dive's address are one
        property; two entries would compete for the same transcript."""
        entries = properties.build(self.DEALS, [], ["1120 Jackson Street"])
        self.assertEqual(len(entries), 1)
        self.assertIn("Site DD", entries[0]["sources"])

    def test_a_property_with_no_deal_still_gets_a_key(self):
        entries = properties.build([], ["Eagle Rock Apartments"], [])
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["has_deal"])
        self.assertTrue(entries[0]["key"].startswith("label:"))

    def test_a_nameless_no_alias_property_is_flagged_as_risky(self):
        entries = properties.build([], ["Eagle Rock Apartments"], [])
        self.assertTrue(entries[0]["match_risk"])
        entries = properties.build([], ["Eagle Rock Apartments"], [],
                                   {"label:eagle rock apartments": ["The Rock"]})
        self.assertFalse(entries[0]["match_risk"])


class CacheKeyTests(unittest.TestCase):
    def test_the_same_query_is_the_same_key(self):
        a = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [3, 1, 2])
        b = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1, 2, 3])
        self.assertEqual(a, b)

    def test_a_new_transcript_invalidates(self):
        a = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1, 2])
        b = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1, 2, 3])
        self.assertNotEqual(a, b)

    def test_a_different_range_invalidates(self):
        a = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1])
        b = synth.cache_key("deal:1", "2026-01-01", "2026-03-31", [1])
        self.assertNotEqual(a, b)

    def test_a_different_property_invalidates(self):
        a = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1])
        b = synth.cache_key("deal:2", "2026-04-01", "2026-06-30", [1])
        self.assertNotEqual(a, b)

    def test_a_prompt_change_invalidates(self):
        a = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1], "v1")
        b = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1], "v2")
        self.assertNotEqual(a, b)


class ParseTests(unittest.TestCase):
    TRANSCRIPTS = [{"id": 7, "transcript_date": "2026-05-14", "title": "Ops sync"},
                   {"id": 8, "transcript_date": "2026-06-02", "title": "Debrief"}]

    def _reply(self, points):
        import json
        return json.dumps({"sections": [{"key": "operations", "points": points}]})

    def test_a_point_is_tagged_with_its_meeting(self):
        out = synth.parse_response(
            self._reply([{"text": "Occupancy up.", "transcript_id": 7}]),
            self.TRANSCRIPTS)
        ops = next(s for s in out if s["key"] == "operations")
        self.assertEqual(ops["points"][0]["date"], "2026-05-14")
        self.assertEqual(ops["points"][0]["title"], "Ops sync")

    def test_an_unattributable_point_is_dropped_not_repaired(self):
        """A citation to a meeting that was not in the query is a
        fabrication. It is removed rather than reassigned to a plausible
        neighbour."""
        out = synth.parse_response(
            self._reply([{"text": "Real.", "transcript_id": 7},
                         {"text": "Invented.", "transcript_id": 999}]),
            self.TRANSCRIPTS)
        ops = next(s for s in out if s["key"] == "operations")
        self.assertEqual([p["text"] for p in ops["points"]], ["Real."])

    def test_the_drop_is_counted_so_it_can_be_reported(self):
        self.assertEqual(synth.dropped_count(
            self._reply([{"text": "a", "transcript_id": 7},
                         {"text": "b", "transcript_id": 999}]),
            self.TRANSCRIPTS), 1)

    def test_a_point_with_no_source_is_dropped(self):
        out = synth.parse_response(self._reply([{"text": "No source."}]),
                                   self.TRANSCRIPTS)
        self.assertEqual(next(s for s in out if s["key"] == "operations")["points"], [])

    def test_all_five_sections_always_come_back_in_order(self):
        out = synth.parse_response("{}", self.TRANSCRIPTS)
        self.assertEqual([s["key"] for s in out], list(synth.SECTION_KEYS))

    def test_a_section_with_nothing_says_so(self):
        out = synth.parse_response("{}", self.TRANSCRIPTS)
        self.assertTrue(all(s["empty"] for s in out))
        self.assertTrue(all(s["empty_text"] == synth.EMPTY_SECTION_TEXT for s in out))

    def test_a_fenced_reply_is_still_parsed(self):
        fenced = "```json\n" + self._reply(
            [{"text": "Fenced.", "transcript_id": 7}]) + "\n```"
        out = synth.parse_response(fenced, self.TRANSCRIPTS)
        self.assertTrue(next(s for s in out if s["key"] == "operations")["points"])

    def test_unparseable_output_yields_empty_sections_not_an_exception(self):
        out = synth.parse_response("the model said something else entirely",
                                   self.TRANSCRIPTS)
        # Derived, not hardcoded: the section list is a product
        # decision that changes, and a literal here turns every
        # such change into an unrelated-looking failure.
        self.assertEqual(len(out), len(synth.SECTIONS))
        self.assertTrue(all(s["empty"] for s in out))


class SizeGuardTests(unittest.TestCase):
    def test_nothing_selected_is_refused(self):
        with self.assertRaises(synth.TooMuchInput):
            synth.check_size([])

    def test_too_many_transcripts_is_refused(self):
        many = [{"id": i, "body": "x"} for i in range(synth.MAX_TRANSCRIPTS + 1)]
        with self.assertRaises(synth.TooMuchInput):
            synth.check_size(many)

    def test_too_much_text_is_refused(self):
        huge = [{"id": 1, "body": "x" * (synth.MAX_TOTAL_CHARS + 1)}]
        with self.assertRaises(synth.TooMuchInput):
            synth.check_size(huge)

    def test_a_normal_quarter_passes(self):
        synth.check_size([{"id": i, "body": "x" * 20_000} for i in range(8)])


class FiguresTests(unittest.TestCase):
    SECTIONS = [{"name": "Capital Improvements", "points": [
        {"text": "Lobby repaint cost about $8,400.", "transcript_id": 7,
         "date": "2026-05-14"}]}]

    def test_spoken_figures_are_extracted_for_display(self):
        found = synth.figures_in(self.SECTIONS)
        self.assertEqual(found[0]["value"], 8400.0)
        self.assertEqual(found[0]["transcript_id"], 7)

    def test_a_divergence_from_the_model_is_reported_not_resolved(self):
        rows = synth.compare_with_model(self.SECTIONS, {"Lobby budget": 6000.0})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["spoken"], 8400.0)
        self.assertEqual(rows[0]["modelled"], 6000.0)
        # No "correct" value is offered -- only both numbers.
        self.assertNotIn("correct", rows[0])

    def test_a_close_figure_is_not_flagged(self):
        self.assertEqual(
            synth.compare_with_model(self.SECTIONS, {"Lobby budget": 8400.0}), [])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "notes.db"

    def test_a_transcript_round_trips(self):
        with notes_db.get_connection(self.path) as conn:
            tid = notes_db.add_transcript(
                conn, body="Jackson occupancy up.", transcript_date="2026-05-14",
                source="fathom", title="Ops sync")
            row = notes_db.get_transcript(conn, tid)
        self.assertEqual(row["transcript_date"], "2026-05-14")
        self.assertEqual(row["match_method"], notes_db.MATCH_UNASSIGNED)

    def test_the_date_filter_is_inclusive_at_both_ends(self):
        with notes_db.get_connection(self.path) as conn:
            for d in ("2026-03-31", "2026-04-01", "2026-06-30", "2026-07-01"):
                notes_db.add_transcript(conn, body="x", transcript_date=d)
            found = notes_db.list_transcripts(conn, start="2026-04-01",
                                              end="2026-06-30")
        self.assertEqual([t["transcript_date"] for t in found],
                         ["2026-04-01", "2026-06-30"])

    def test_an_unknown_source_falls_back_rather_than_raising(self):
        with notes_db.get_connection(self.path) as conn:
            tid = notes_db.add_transcript(conn, body="x", transcript_date="2026-01-01",
                                          source="telepathy")
            self.assertEqual(notes_db.get_transcript(conn, tid)["source"],
                             notes_db.SOURCE_UNSPECIFIED)

    def test_a_duplicate_alias_is_not_an_error(self):
        with notes_db.get_connection(self.path) as conn:
            self.assertTrue(notes_db.add_alias(conn, "deal:1", "Jackson"))
            self.assertFalse(notes_db.add_alias(conn, "deal:1", "Jackson"))

    def test_an_update_is_keyed_on_its_query(self):
        with notes_db.get_connection(self.path) as conn:
            notes_db.save_update(
                conn, property_key="deal:1", property_label="Jackson",
                period_start="2026-04-01", period_end="2026-06-30",
                cache_key="abc", prompt_version="v1", model="m",
                sections=[{"key": "operations"}], transcript_ids=[1, 2])
            self.assertIsNotNone(notes_db.find_update(conn, "abc"))
            self.assertIsNone(notes_db.find_update(conn, "def"))

    def test_regenerating_replaces_rather_than_duplicating(self):
        with notes_db.get_connection(self.path) as conn:
            for _ in range(2):
                notes_db.save_update(
                    conn, property_key="deal:1", property_label="Jackson",
                    period_start="2026-04-01", period_end="2026-06-30",
                    cache_key="abc", prompt_version="v1", model="m",
                    sections=[], transcript_ids=[1])
            self.assertEqual(len(notes_db.list_updates(conn)), 1)

    def test_the_storage_path_follows_the_env_var_pattern(self):
        import os
        old = os.environ.get("INVESTOR_NOTES_DB_PATH")
        try:
            os.environ["INVESTOR_NOTES_DB_PATH"] = str(self.path)
            self.assertEqual(notes_db.get_db_path(), self.path)
            self.assertTrue(notes_db.storage_status()["persistent"])
            os.environ["INVESTOR_NOTES_DB_PATH"] = ""
            self.assertFalse(notes_db.storage_status()["persistent"])
        finally:
            if old is None:
                os.environ.pop("INVESTOR_NOTES_DB_PATH", None)
            else:
                os.environ["INVESTOR_NOTES_DB_PATH"] = old


class UploadLimitTests(unittest.TestCase):
    def test_the_endpoint_has_its_own_limit(self):
        self.assertIn("investor_notes.upload", ul.ENDPOINT_LIMITS)
        self.assertEqual(ul.limit_for("investor_notes.upload"),
                         ul.TRANSCRIPT_BYTES)

    def test_it_is_smaller_than_the_spreadsheet_limit(self):
        """A transcript is text. Something 20 MB arriving here is not one."""
        self.assertLess(ul.TRANSCRIPT_BYTES, ul.SPREADSHEET_BYTES)


class IsolationTests(unittest.TestCase):
    def test_nothing_in_this_feature_writes_to_another_tool(self):
        """The rule that matters most: a figure said on a call must never
        reach the underwriting model."""
        import re
        for name in ("investor_notes.py", "investor_notes_synth.py",
                     "investor_notes_db.py", "investor_notes_match.py",
                     "investor_notes_properties.py", "investor_notes_export.py"):
            text = Path("tools") / name
            src = text.read_text(encoding="utf-8")
            for forbidden in ("upsert_findings", "replace_capex_lines",
                              "update_scenario", "create_deal", "update_deal",
                              "replace_expense_lines", "replace_unit_lines"):
                with self.subTest(module=name, call=forbidden):
                    self.assertNotIn(forbidden, src)

    def test_it_only_ever_reads_from_the_other_tools(self):
        src = (Path("tools") / "investor_notes.py").read_text(encoding="utf-8")
        self.assertNotIn("INSERT INTO deals", src)
        self.assertNotIn("UPDATE underwriting", src)




if __name__ == "__main__":
    unittest.main()


class ConfirmedSectionSetTests(unittest.TestCase):
    """The section list Michelle confirmed, and the version bump it forces.

    She was asked whether to add Legal Update and Next Steps and to
    rename two headings. Her answer: "don't worry about the legal update
    but include a capex update and next steps." Property Update was never
    mentioned, so Operations keeps its name -- renaming it would be an
    invention, and the rename is not free.

    IT IS NOT FREE BECAUSE THE NAMES REACH THE MODEL

    build_instructions() interpolates each section's name straight into
    the prompt, so this is a prompt change and not a display change.
    cache_key() hashes PROMPT_VERSION rather than the prompt text, which
    means a rename WITHOUT a version bump would serve results generated
    under the old headings as though they came from the new ones. The
    bump is the thing that prevents that, so it is asserted here.
    """

    def names(self):
        return [s["name"] for s in synth.SECTIONS]

    def test_capex_update_replaces_capital_improvements(self):
        self.assertIn("CapEx Update", self.names())
        self.assertNotIn("Capital Improvements", self.names())

    def test_next_steps_is_present(self):
        self.assertIn("Next Steps", self.names())

    def test_legal_update_is_absent(self):
        """She asked for it to be skipped."""
        self.assertNotIn("Legal Update", self.names())

    def test_operations_keeps_its_name(self):
        """Property Update was not mentioned; renaming it is unevidenced."""
        self.assertIn("Operations", self.names())

    def test_the_key_did_not_change_with_the_label(self):
        """Renaming the heading must not orphan stored sections_json."""
        self.assertIn("capital_improvements", synth.SECTION_KEYS)

    def test_next_steps_has_a_key_and_a_brief(self):
        entry = next(s for s in synth.SECTIONS if s["key"] == "next_steps")
        self.assertTrue(entry["brief"].strip())

    def test_the_brief_forbids_inventing_a_plan(self):
        """The section most likely to tempt the model into helpfulness."""
        brief = next(s for s in synth.SECTIONS
                     if s["key"] == "next_steps")["brief"].lower()
        self.assertIn("never", brief)

    def test_the_new_names_actually_reach_the_prompt(self):
        instructions = synth.build_instructions()
        self.assertIn("CapEx Update", instructions)
        self.assertIn("Next Steps", instructions)
        self.assertNotIn("Capital Improvements", instructions)

    def test_the_prompt_version_was_bumped(self):
        self.assertEqual(synth.PROMPT_VERSION, "investor_update_v2")

    def test_the_bump_invalidates_the_old_cache(self):
        a = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1],
                            prompt_version="investor_update_v1")
        b = synth.cache_key("deal:1", "2026-04-01", "2026-06-30", [1],
                            prompt_version="investor_update_v2")
        self.assertNotEqual(a, b)

    def test_every_section_still_parses_into_a_result(self):
        out = synth.parse_response("not json at all", self.TRANSCRIPTS
                                   if hasattr(self, "TRANSCRIPTS") else [])
        self.assertEqual(len(out), len(synth.SECTIONS))
        self.assertTrue(all(s["empty"] for s in out))
