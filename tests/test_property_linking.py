"""A record that names its deal belongs to that deal.

WHAT THIS FIXES, AND HOW IT WAS FOUND

Absorption into a Deal Dive entry worked only by normalising a label
against the deal's address. Site DD's "19 bay vista drive" merged with
deal 1 because it IS the address. "Nabob Hill" did not merge with deal 2,
because a building's name is not its address -- so a property that
already existed acquired a second, rival registry entry.

That rivalry was not cosmetic. Adding "Nabob Hill" as an alias of deal:2
while label:nabob hill still existed made BOTH entries claim the phrase
with equal weight, and the matcher -- correctly refusing to choose
between two equal candidates -- then matched nothing at all. A transcript
naming the property stopped being assignable. The alias had to be
reverted on production.

An explicit deal_id is a person stating that these are the same
property. That is better evidence than a string comparison, so it wins.

THE ORDER MATTERS OPERATIONALLY

The alias is only safe once this change is live. Re-adding it while the
old builder is deployed reproduces the ambiguity exactly.
"""

import unittest

from tools import investor_notes_properties as props
from tools import investor_notes_match as match

DEALS = [
    {"id": 1, "address": "19 Bay Vista Drive", "city": "Mill Valley", "state": "CA"},
    {"id": 2, "address": "1120 Jackson Street", "city": "San Francisco", "state": "CA"},
]
BODY = "We walked Nabob Hill. Nabob Hill has 16 units. Nabob Hill needs paint."


class DealIdFoldsTests(unittest.TestCase):
    def build(self, sd, aliases=None):
        return props.build(DEALS, [], sd, aliases or {})

    def test_a_linked_label_does_not_spawn_a_rival_entry(self):
        entries = self.build([("Nabob Hill", 2)])
        self.assertEqual([e["key"] for e in entries], ["deal:2", "deal:1"])

    def test_the_link_beats_the_name(self):
        """The whole point: 'Nabob Hill' is not '1120 Jackson Street'."""
        entries = self.build([("Nabob Hill", 2)])
        self.assertFalse(any("nabob" in e["key"] for e in entries))

    def test_the_local_name_survives_as_an_alias(self):
        entries = self.build([("Nabob Hill", 2)])
        d2 = next(e for e in entries if e["key"] == "deal:2")
        self.assertIn("Nabob Hill", d2["aliases"])

    def test_the_source_is_recorded(self):
        d2 = next(e for e in self.build([("Nabob Hill", 2)])
                  if e["key"] == "deal:2")
        self.assertIn("Site DD", d2["sources"])

    def test_an_unlinked_label_still_spawns_its_own_entry(self):
        entries = self.build([("Nabob Hill", None)])
        self.assertTrue(any(e["key"] == "label:nabob hill" for e in entries))

    def test_address_absorption_still_works(self):
        """The pre-existing path must not regress."""
        entries = self.build([("19 bay vista drive", None)])
        d1 = next(e for e in entries if e["key"] == "deal:1")
        self.assertIn("Site DD", d1["sources"])
        self.assertFalse(any("bay vista" in e["key"] for e in entries))

    def test_a_deal_id_pointing_nowhere_falls_back_to_the_name(self):
        """A dangling link must not silently swallow the record."""
        entries = self.build([("Ghost Property", 999)])
        self.assertTrue(any(e["key"] == "label:ghost property" for e in entries))

    def test_plain_string_labels_are_still_accepted(self):
        """Backward compatible: not every caller passes pairs."""
        entries = props.build(DEALS, ["Eagle Rock Apartments"], [], {})
        self.assertTrue(any(e["key"] == "label:eagle rock apartments"
                            for e in entries))


class TheAliasIsSafeNowTests(unittest.TestCase):
    """The regression that forced the revert, asserted both ways."""

    def test_without_the_fix_the_alias_would_be_ambiguous(self):
        """Two entries, both claiming the phrase: the old shape."""
        entries = props.build(DEALS, [], [("Nabob Hill", None)],
                              {"deal:2": ["Jackson", "Nabob Hill"]})
        self.assertEqual(match.match(BODY, entries)["outcome"], "ambiguous")

    def test_with_the_fix_it_resolves_to_the_deal(self):
        entries = props.build(DEALS, [], [("Nabob Hill", 2)],
                              {"deal:2": ["Jackson", "Nabob Hill"]})
        result = match.match(BODY, entries)
        self.assertEqual(result["outcome"], "matched")
        self.assertEqual(result["key"], "deal:2")


class NothingElseStoppedResolvingTests(unittest.TestCase):
    """Investor Notes, Market Context and the alias table all read this."""

    def test_every_underwriting_label_still_has_an_entry(self):
        labels = ["Eagle Rock Apartments", "Oxford Pointe", "The Canyon Apartments",
                  "Maple Valley Apartments", "Waterways", "The View",
                  "River Oaks", "Cannongate", "Papania"]
        entries = props.build(DEALS, [(l, None) for l in labels],
                              [("Nabob Hill", 2)], {})
        keys = {e["key"] for e in entries}
        for l in labels:
            with self.subTest(label=l):
                self.assertIn(props.label_key(l), keys)

    def test_the_registry_shrinks_by_exactly_the_folded_entry(self):
        labels = [(f"P{i}", None) for i in range(9)]
        unlinked = props.build(DEALS, labels, [("Nabob Hill", None)], {})
        linked = props.build(DEALS, labels, [("Nabob Hill", 2)], {})
        self.assertEqual(len(unlinked) - len(linked), 1)


class DealsCarryPropertyDetailTests(unittest.TestCase):
    """Step 1: nullable, additive, nothing reads them yet."""

    def test_the_four_columns_are_declared(self):
        from tools import deal_dive_db as ddb
        names = {n for n, _ in ddb._DEAL_ADDED_COLUMNS}
        self.assertEqual(names, {"name", "vintage", "building_count",
                                 "property_sqft"})

    def test_vintage_is_text_because_vintages_are_written_as_ranges(self):
        from tools import deal_dive_db as ddb
        self.assertEqual(dict(ddb._DEAL_ADDED_COLUMNS)["vintage"], "TEXT")

    def test_the_migration_is_idempotent(self):
        import sqlite3, tempfile, os
        from tools import deal_dive_db as ddb
        path = os.path.join(tempfile.mkdtemp(), "d.db")
        for _ in range(3):
            c = sqlite3.connect(path); ddb.init_schema(c); c.close()
        c = sqlite3.connect(path)
        cols = [r[1] for r in c.execute("PRAGMA table_info(deals)")]
        c.close()
        for n, _ in ddb._DEAL_ADDED_COLUMNS:
            self.assertEqual(cols.count(n), 1)


if __name__ == "__main__":
    unittest.main()
