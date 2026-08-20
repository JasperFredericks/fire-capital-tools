"""A rule stated twice must not lose its condition in the shorter statement.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT

It is a **regression guard over named rules**, not a discovery instrument.
It cannot find a new two-statement drift; it can only stop a registered one
from being re-stripped. That limitation is chosen, and the measurements
behind the choice are recorded below so nobody re-runs the experiment.

THE DISCOVERY VERSION WAS BUILT, MEASURED, AND REFUSED

Three designs were tried against a HANDOFF known to be correct. The
question each had to answer: "which rules are stated in two places, with a
condition in one and not the other?"

  v1  cluster on the content words INSIDE each imperative.
      FAILED ITS POSITIVE CONTROL. Restoring the real pre-Part-41 text --
      "Do not start the Entrata parser seam" against "Do not scope it
      until a sample exists" -- produced ZERO pairs, because the long form
      says "it". The topic is in the surrounding sentence, never in the
      verb phrase. An instrument that misses the one case it was built for
      is not a strict instrument, it is a broken one.

  v2  discover topics from capitalised nouns and backticked identifiers.
      2 flags on a correct file, both false: markdown split mid-statement,
      and the new HANDOFF section that QUOTES stripped rules as examples.

  v3  join paragraphs, strip quoted spans. 3 flags on a correct file, all
      false -- generic topic words ("Site", "Closed") grouping unrelated
      statements, and bullet lists joined into one unit.

  100% false positives on every design that could discover anything.

THE ROOT CAUSE IS LINGUISTIC, NOT A TUNING PROBLEM

Measured: of 16 uses of "never" in HANDOFF, **13 are descriptive** --
"never shipped", "never exercised", "the rate bug never touched equity".
Only 3 are prescriptive. Telling a rule from a sentence about the past is
a natural-language judgment, and a regex that tries it flags prose.

The dead-reader glob was measured at 81 hits / 54 framework routes and
refused on the same grounds; the noisy half is refused here and the narrow
half kept, which is the same trade.

So: the convention is the real protection --

    when writing a rule in a summary, carry its condition or link the
    full statement

-- and this file holds the line only for rules already known to have
drifted. Adding a topic here is cheap; discovering one is human work.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "HANDOFF.md"

PROHIBITION = re.compile(r"\b(do not|don't|must not|does not want|"
                         r"deliberately unscoped)\b", re.I)

# Either a real condition, or a pointer to the statement that carries one.
CONDITION = re.compile(r"\b(until|unless|whenever|before|after|if\b|while|"
                       r"as long as|once\b|except|provided|pending)\b", re.I)
CROSSREF = re.compile(r"(see |full statement|also stated|keep the two in step|"
                      r"what that does and does not cover|\]\(#)", re.I)

# ONE SECTION IS EXCLUDED, WITH A REASON, AND THE EXCLUSION SELF-CHECKS
#
# HANDOFF now contains a section that documents this very failure, and it
# does so by NAMING the rules that drifted and quoting what they used to
# say. Prose about a prohibition is lexically a prohibition: "the extra
# scope forbids something valuable ... separates these three from
# normalize_address_key and Entrata" matches on both the topic and the
# verb, and it is not a rule at all.
#
# Stripping quotations handles the quoted half; it cannot handle a sentence
# that merely discusses the rule. That is a natural-language judgment, so
# the section is excluded by name rather than by a cleverer pattern, and
# test_the_excluded_section_still_exists fails if it is renamed -- the same
# stale-entry self-check both reachability sweeps carry.
SKIP_SECTIONS = {
    "A rule stated twice loses its condition in the shorter statement":
        "documents the drift failure by naming and quoting the rules that "
        "drifted; every statement in it is commentary, not a rule",
}

# WHY ONLY ONE RULE IS REGISTERED, THOUGH TWO WERE NARROWED
#
# Entrata's qualifier is a CONDITION -- "until a real sample export exists"
# -- and condition words are a closed lexical class, so its absence is
# mechanically detectable. The positive control fires precisely on it.
#
# Scraping's qualifier is a DOMAIN -- which sites it covers, and that
# licensed APIs are exempt -- and that is NOT mechanically detectable.
# Measured, not assumed: the original bare rule read "Michelle explicitly
# does not want scraping; reference costs are a one-time manual research
# pass", which contains "reference cost". Every domain-shaped pattern loose
# enough to accept the narrowed rule's own paragraphs also accepts that
# original, and every pattern tight enough to reject the original (requiring
# the licensed-API exemption) rejects narrowed paragraphs that are perfectly
# well scoped. Its positive control could not be made to fire without
# tuning the pattern until the two known strings happened to land right --
# which is fitting the instrument to its test cases, and would have shipped
# a check that certifies nothing.
#
# So scraping is deliberately NOT registered. Judging whether a domain is
# adequately stated is a semantic judgment and stays with the reader.
# The convention in HANDOFF covers it; this file covers what a regex can
# honestly decide.
REGISTERED_RULES = {
    "entrata": {
        "qualifier": lambda body: bool(CONDITION.search(body)
                                       or CROSSREF.search(body)),
        "needs": "a condition or a cross-reference to the full statement",
        "note": ("Stated in 'Open operational items' and in 'Revised cost "
                 "estimates'. The short form read 'Do not start the Entrata "
                 "parser seam' with no condition, no reason and no exit "
                 "criterion, while the long form carried all three. "
                 "Narrowed in Part 41."),
    },
}


def _units(text: str) -> list[tuple[int, str]]:
    """Paragraphs and table cells, each flattened to one string.

    A rule is a paragraph, not a source line. Splitting on newlines cuts
    statements in half and leaves fragments that look like bare
    prohibitions because their condition is on the next line -- which is
    exactly the false positive v2 produced.
    """
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 1
    skipping = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            if buf:
                out.append((start, " ".join(buf)))
                buf = []
            skipping = line[3:].strip() in SKIP_SECTIONS
            continue
        if skipping:
            continue
        if line.strip().startswith("|"):
            if buf:
                out.append((start, " ".join(buf)))
                buf = []
            for cell in line.split("|"):
                if cell.strip():
                    out.append((lineno, cell.strip()))
            continue
        if not line.strip():
            if buf:
                out.append((start, " ".join(buf)))
                buf = []
            continue
        # A BULLET IS ITS OWN UNIT, AND MISSING THIS MADE THE CHECK BLIND
        #
        # "Open operational items" is a contiguous run of bullets with no
        # blank lines between them. Joined as one paragraph, the bare
        # Entrata bullet inherited condition words from its NEIGHBOURS
        # ("if", "once", "before" in unrelated entries) and the check
        # passed on a file that was deliberately broken. The positive
        # control caught it; nothing else would have.
        if re.match(r"[-*+]\s+\S", line.strip()):
            if buf:
                out.append((start, " ".join(buf)))
            buf = [line.strip()]
            start = lineno
            continue
        if not buf:
            start = lineno
        buf.append(line.strip())
    if buf:
        out.append((start, " ".join(buf)))
    return out


def _without_quotations(s: str) -> str:
    """Quoting a rule in order to discuss it is not stating it.

    HANDOFF documents this failure by reproducing stripped rules verbatim.
    A check that cannot tell a quotation from a statement flags the
    documentation of the problem as an instance of the problem -- which is
    precisely what v2 did.
    """
    return re.sub(r'"[^"]{0,400}"', " ", s)


def statements_about(topic: str) -> list[tuple[int, str]]:
    text = HANDOFF.read_text(encoding="utf-8")
    found = []
    for lineno, raw in _units(text):
        body = _without_quotations(raw)
        if topic in body.lower() and PROHIBITION.search(body):
            found.append((lineno, body))
    return found


class EveryStatementOfARegisteredRuleCarriesItsScopeTests(unittest.TestCase):
    """The invariant: a prohibition names its own limit, or points at it."""

    def test_handoff_exists(self):
        self.assertTrue(HANDOFF.is_file())

    def test_the_excluded_section_still_exists(self):
        """A stale exclusion silently widens what the check ignores."""
        headings = {l[3:].strip() for l in
                    HANDOFF.read_text(encoding="utf-8").splitlines()
                    if l.startswith("## ")}
        for name, why in SKIP_SECTIONS.items():
            with self.subTest(section=name):
                self.assertIn(name, headings,
                              f"excluded section is gone; drop it from "
                              f"SKIP_SECTIONS (reason was: {why})")

    def test_registered_rules_are_still_stated_somewhere(self):
        """A topic that matches nothing is a stale registry entry.

        Both sweeps in this repo self-check for stale allowlist entries;
        this does the same, so a rule that gets deleted or reworded out of
        existence does not leave a check quietly certifying nothing.
        """
        for topic in REGISTERED_RULES:
            with self.subTest(topic=topic):
                self.assertTrue(
                    statements_about(topic),
                    f"no prohibition mentions {topic!r} any more -- remove it "
                    f"from REGISTERED_RULES or restore the rule")

    def test_no_statement_is_a_bare_prohibition(self):
        offenders = []
        for topic, rule in REGISTERED_RULES.items():
            for lineno, body in statements_about(topic):
                if not rule["qualifier"](body):
                    offenders.append(
                        f"HANDOFF.md:{lineno} states {topic!r} without "
                        f"{rule['needs']}:\n"
                        f"      {body[:160]}\n"
                        f"    context: {rule['note']}")
        self.assertEqual(
            offenders, [],
            "a rule stated in a summary must carry its condition or link "
            "the full statement:\n  " + "\n  ".join(offenders))


class TheCheckIsNotVacuousTests(unittest.TestCase):
    """An instrument that has never returned a difference has not been tested."""

    def test_the_prohibition_pattern_matches_a_bare_rule(self):
        self.assertTrue(PROHIBITION.search("Do not start the Entrata parser seam."))
        self.assertTrue(PROHIBITION.search("Michelle explicitly does not want scraping"))

    def test_a_bare_prohibition_is_recognised_as_bare(self):
        bare = "Do not start the Entrata parser seam."
        self.assertIsNone(CONDITION.search(bare))
        self.assertIsNone(CROSSREF.search(bare))

    def test_a_conditioned_prohibition_passes(self):
        ok = "Do not scope the Entrata parser seam until a real sample export exists."
        self.assertTrue(CONDITION.search(ok))

    def test_a_cross_reference_counts_as_carrying_the_condition(self):
        ok = "Do not start the Entrata parser seam. Full statement in Revised cost estimates."
        self.assertTrue(CROSSREF.search(ok))

    def test_quotations_are_not_read_as_statements(self):
        quoting = ('The line read "Do not start the Entrata parser seam" '
                   'without its condition.')
        self.assertIsNone(PROHIBITION.search(_without_quotations(quoting)))

    def test_a_paragraph_keeps_its_condition_when_flattened(self):
        """The v2 false positive, pinned so it cannot come back."""
        text = "- **Do not scope the Entrata seam\n  until a sample exists.**\n"
        joined = _units(text)[0][1]
        self.assertTrue(CONDITION.search(joined))


if __name__ == "__main__":
    unittest.main()
