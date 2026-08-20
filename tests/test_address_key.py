"""normalize_address_key: what it may merge, and what it must never merge.

There were no tests for this function at all, which is how a decision
about it survived fifteen runs without anybody checking what it was aimed
at. The point of this file is less the ZIP5 truncation -- that is four
lines -- than PINNING THE COLLISION RULE beside it, so the next person who
wants to fix the duplicate-address problem by dropping street suffixes
fails a test instead of shipping it.

The two cases are not the same and the tests say so in both directions:

    a ZIP+4 and its ZIP5      SAME address    -> must merge
    Main St / Ave / Blvd      DIFFERENT       -> must never merge
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.market_data_cache import normalize_address_key as key, zip5


class ZipPlusFourIsTheSameAddressTests(unittest.TestCase):
    """Deal 1 could not hit its own cached data.

    It stores 94941-1604; the cached row was written from a ZIP5 entry. So
    every Rent Comps open spent a fresh RentCast call against a 50/month
    budget for data already in the table.
    """

    def test_a_zip_plus_four_keys_the_same_as_its_zip5(self):
        self.assertEqual(
            key("19 Bay Vista Drive", "Mill Valley", "CA", "94941-1604"),
            key("19 Bay Vista Drive", "Mill Valley", "CA", "94941"))

    def test_deal_one_now_hits_its_cached_row(self):
        """The exact production pair, as it stood on 2026-08-20."""
        self.assertEqual(
            key("19 Bay Vista Drive", "Mill Valley", "CA", "94941-1604"),
            "19 bay vista drive mill valley ca 94941")

    def test_two_plus_fours_under_one_zip5_are_one_key(self):
        self.assertEqual(key("100 Main St", "Springfield", "IL", "62704-1111"),
                         key("100 Main St", "Springfield", "IL", "62704-2222"))


class ItMustNotMergeDifferentAddressesTests(unittest.TestCase):
    """The Part 23 collision rule, pinned.

    Dropping the street type collapses `100 Main St`, `100 Main Ave` and
    `100 Main Blvd` to `100 main`. They coexist in real cities and serving
    one street's comps for another is far worse than a wasted call. That
    decision stands; this is the test that keeps it standing.
    """

    def assertDistinct(self, a, b, why):
        self.assertNotEqual(key(*a), key(*b), why)

    def test_street_types_are_not_normalised(self):
        base = ("100 Main", "Springfield", "IL", "62704")
        st = ("100 Main St",) + base[1:]
        ave = ("100 Main Ave",) + base[1:]
        blvd = ("100 Main Blvd",) + base[1:]
        self.assertDistinct(st, ave, "St and Ave are different streets")
        self.assertDistinct(st, blvd, "St and Blvd are different streets")
        self.assertDistinct(ave, blvd, "Ave and Blvd are different streets")

    def test_a_missing_suffix_still_makes_a_different_key(self):
        """24 Steiner vs 24 Steiner Street stay separate.

        These are the real duplicate rows. Merging them is a job for a
        human at entry -- see docs/address-normalize-at-entry.md -- not for
        the key function, because the transformation that would do it is
        the one ruled out above.
        """
        self.assertDistinct(("24 Steiner", "San Francisco", "CA", "94117"),
                            ("24 Steiner Street", "San Francisco", "CA", "94117"),
                            "suffix handling is deliberately absent")

    def test_house_numbers_are_not_merged(self):
        self.assertDistinct(("22 Steiner St", "San Francisco", "CA", "94117"),
                            ("24 Steiner St", "San Francisco", "CA", "94117"),
                            "22 and 24 Steiner are two buildings")

    def test_different_zip5_stays_different(self):
        self.assertDistinct(("100 Main St", "Springfield", "IL", "62704"),
                            ("100 Main St", "Springfield", "IL", "62705"),
                            "only the +4 is dropped, never a zip5 digit")

    def test_state_still_separates(self):
        self.assertDistinct(("100 Main St", "Springfield", "IL", "62704-1111"),
                            ("100 Main St", "Springfield", "MA", "62704-1111"),
                            "truncation must not reach past the zip")

    def test_a_unit_lives_in_the_address_line_so_units_stay_distinct(self):
        """The +4 carries nothing the address line does not.

        This is the argument that makes truncation safe: if two records are
        genuinely different units, the unit designator is in the address
        line, so they differ there and their keys differ regardless of what
        happens to the zip.
        """
        self.assertDistinct(("100 Main St Apt 1", "Springfield", "IL", "62704-1111"),
                            ("100 Main St Apt 2", "Springfield", "IL", "62704-2222"),
                            "units differ in the address line")


class Zip5IsConservativeTests(unittest.TestCase):
    """Anything not exactly NNNNN-NNNN is returned unchanged, not guessed at."""

    def test_it_leaves_everything_else_alone(self):
        for odd in ("94941", "9494", "941", "", "SW1A 1AA", "94941-16",
                    "94941-16045", "abcde-fghi", "94941 1604"):
            self.assertEqual(zip5(odd), odd.strip(),
                             f"{odd!r} should pass through untouched")

    def test_none_and_blank_behave_as_before(self):
        self.assertEqual(zip5(None), "")
        self.assertEqual(zip5("   "), "")
        self.assertEqual(key("100 Main St", "Springfield", "IL", None),
                         "100 main st springfield il")


class ItOrphansNothingTests(unittest.TestCase):
    """Changing a key function strands every row written under the old one.

    That is the argument that blocked this change, and it is a real one --
    it just does not apply here, because no cached row carries a ZIP+4.
    Verified against production on 2026-08-20: 12 rows, zero with a hyphen
    in the zip, and market_data_cache is the only table in /data holding an
    address_key at all.

    These are the twelve production keys. Every one must be produced
    unchanged by the new function, or a live row has been orphaned.
    """

    PRODUCTION_ROWS = [
        (("1029 S Jackson St.", "Seattle", "WA", None),
         "1029 s jackson st. seattle wa"),
        (("1120 Jackson Street", "San Francisco", "CA", "94133"),
         "1120 jackson street san francisco ca 94133"),
        (("11602 Apex View Dr.", "Louisville", "KY", None),
         "11602 apex view dr. louisville ky"),
        (("1317 S Mellonville Ave.", "Sanford", "FL", None),
         "1317 s mellonville ave. sanford fl"),
        (("19 Bay Vista Drive", "Mill Valley", "CA", "94941"),
         "19 bay vista drive mill valley ca 94941"),
        (("22 Steiner St", "San Francisco", "CA", "94117"),
         "22 steiner st san francisco ca 94117"),
        (("24 Steiner", "San Francisco", "CA", "94117"),
         "24 steiner san francisco ca 94117"),
        (("24 Steiner Street", "San Francisco", "CA", "94117"),
         "24 steiner street san francisco ca 94117"),
        (("480 Warren Dr", "San Francisco", "CA", None),
         "480 warren dr san francisco ca"),
        (("5208 11th Street", "Lubbock", "TX", "79416"),
         "5208 11th street lubbock tx 79416"),
        (("598 Belvedere", "San Francisco", "CA", "94117"),
         "598 belvedere san francisco ca 94117"),
        (("598 Belvedere Street", "San Francisco", "CA", "94117"),
         "598 belvedere street san francisco ca 94117"),
    ]

    def test_every_existing_row_keeps_its_key(self):
        for args, expected in self.PRODUCTION_ROWS:
            self.assertEqual(key(*args), expected,
                             f"{args[0]!r} would be orphaned")

    def test_no_production_key_carries_a_zip_plus_four(self):
        """If this ever fails, the orphaning argument above has expired."""
        for _, expected in self.PRODUCTION_ROWS:
            self.assertIsNone(re.search(r"\d{5}-\d{4}", expected))

    def test_the_twelve_rows_remain_twelve_distinct_keys(self):
        keys = {key(*args) for args, _ in self.PRODUCTION_ROWS}
        self.assertEqual(len(keys), 12,
                         "truncation must not merge two existing rows")


if __name__ == "__main__":
    unittest.main()
