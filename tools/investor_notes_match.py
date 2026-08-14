"""
FIRE Capital Tools - matching a transcript to a property.

Pure: no Flask, no database. Candidates in, ranked evidence out.

WHY SUBSTRING MATCHING AND NOT A MODEL

It is free, instant, and it can show its working. A match that says
"Jackson appears 11 times, Eagle Rock once" is checkable by the person
reading it; a model that says "I think this is Jackson" is not. With a
two-property portfolio the cheap path resolves nearly everything, and
the shared OpenAI budget is better spent on synthesis than on a string
comparison.

WHY DISTINCT-MENTION COUNT AND NOT FIRST HIT

A call about Jackson that opens "before we get to Jackson, quick note on
Eagle Rock" would match Eagle Rock on first hit and be wrong. Counting
mentions across the whole transcript makes the passing reference lose to
the actual subject, which is the behaviour a reader expects.

Counted per ALIAS, then summed per property, and the phrase list is
deduplicated -- so a property with six aliases does not beat one with
two by having more ways to say the same thing.

WHY IT REFUSES TO GUESS

Three outcomes, and only one of them assigns anything:

  matched     one property clearly ahead. Assigned, with its evidence
              shown, and overridable before synthesis runs.
  ambiguous   two or more properties within MARGIN of each other. A call
              genuinely can cover two properties, and the answer may be
              "both" -- which only a person can say.
  unassigned  nothing matched. Listed for one-click assignment rather
              than dropped.

The thresholds are deliberately conservative. A wrong auto-match puts a
property's operations into another property's investor update, which is
a worse failure than asking.
"""

from __future__ import annotations

import re
from typing import Any

# A property must be mentioned at least this many times to be matched at
# all. One passing mention is not what a meeting was about.
MIN_MENTIONS = 2

# The leader must have at least this many more mentions than the runner
# -up, or the result is ambiguous. A 3-vs-2 split is not a decision.
MARGIN = 2

# Aliases shorter than this are ignored unless they are matched as whole
# words anyway -- see _pattern. "1120" is fine; "LA" would match "flat".
SHORT_ALIAS_LEN = 4

_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, collapse punctuation and whitespace to single spaces.

    Keeps digits: street numbers are some of the most distinctive tokens
    in an address, and "1120" is often what someone says.
    """
    return " ".join(_WORD.findall((text or "").lower()))


def _pattern(alias: str) -> re.Pattern | None:
    """A whole-token matcher for one alias.

    Word-boundary anchored so "Bay Vista" does not match inside another
    word, and so a short alias cannot match a fragment. Returns None for
    an alias that normalises to nothing.
    """
    norm = normalize(alias)
    if not norm:
        return None
    return re.compile(r"(?<![a-z0-9])" + re.escape(norm) + r"(?![a-z0-9])")


def count_mentions(body_norm: str, alias: str) -> int:
    pat = _pattern(alias)
    if pat is None:
        return 0
    return len(pat.findall(body_norm))


# Street types and property-type words, stripped when deriving the short
# form of a name. "1120 Jackson Street" -> "Jackson"; "Eagle Rock
# Apartments" -> "Eagle Rock".
STREET_SUFFIXES = {
    "street", "st", "drive", "dr", "avenue", "ave", "road", "rd",
    "boulevard", "blvd", "lane", "ln", "way", "court", "ct", "place", "pl",
    "terrace", "ter", "circle", "cir", "parkway", "pkwy", "highway", "hwy",
}
PROPERTY_SUFFIXES = {
    "apartments", "apartment", "apts", "apt", "residences", "residence",
    "commons", "villas", "towers", "tower", "lofts", "loft", "estates",
    "gardens", "manor", "house", "homes", "park", "place", "square",
}

# A derived short form must be at least this long to be used. Guards
# against a one-word street like "Elm Street" reducing to something that
# collides with ordinary speech.
MIN_DERIVED_LEN = 4


def derive_short_form(name: str, suffixes: set[str]) -> str | None:
    """The name people actually say, from the name on the record.

    Nobody says "one thousand one hundred and twenty Jackson Street" on a
    call. They say "Jackson". Without this, matching against Deal Dive --
    which has no name column, only addresses -- finds the full address
    once in a document header and nothing else, and every real mention is
    missed.

    Conservative on purpose: a leading house number and ONE trailing
    suffix are removed, and the result is rejected if it is too short to
    be distinctive. Anything more aggressive starts matching ordinary
    words, and a wrong auto-match puts one property's operations into
    another property's investor update.
    """
    tokens = normalize(name).split()
    if not tokens:
        return None
    if tokens[0].isdigit():
        tokens = tokens[1:]
    if len(tokens) > 1 and tokens[-1] in suffixes:
        tokens = tokens[:-1]
    short = " ".join(tokens)
    if not short or len(short) < MIN_DERIVED_LEN:
        return None
    if short == normalize(name):
        return None          # nothing was actually removed
    return short


def phrases_for(candidate: dict[str, Any]) -> list[str]:
    """Everything this property can be called, deduplicated.

    The label, whatever parts of an address are distinctive on their own,
    and the aliases a person typed. Deduplicated case-insensitively so a
    property is not advantaged by listing the same phrase twice.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        norm = normalize(value or "")
        if norm and norm not in seen:
            seen.add(norm)
            out.append(value.strip())

    label = candidate.get("label")
    add(label)
    for alias in candidate.get("aliases") or ():
        add(alias)

    # The street line without its city/state tail. "19 Bay Vista Drive"
    # is said far more often than the full record.
    address = candidate.get("address")
    if address:
        add(address)
        head = address.split(",")[0].strip()
        if head and head != address:
            add(head)

    # The short forms. These are what actually get said, and without them
    # a Deal Dive property -- address only, no name column -- is matchable
    # only by a header line.
    if address:
        add(derive_short_form(address.split(",")[0], STREET_SUFFIXES))
    if label:
        head = label.split(",")[0]
        add(derive_short_form(head, PROPERTY_SUFFIXES))
        add(derive_short_form(head, STREET_SUFFIXES))
    return out


def _distinct_spans(spans: list[tuple[int, int]]) -> int:
    """How many separate places in the text were matched.

    Overlapping spans are one mention. Without this, every extra way of
    writing a property's name inflates its score against a property with
    fewer spellings -- which is scoring the catalogue, not the
    conversation.
    """
    if not spans:
        return 0
    merged = 0
    end = -1
    for start, stop in sorted(spans):
        if start >= end:
            merged += 1
            end = stop
        else:
            end = max(end, stop)
    return merged


def score(body: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank candidates by how often each is actually talked about.

    Every candidate comes back, scored, including those with zero -- the
    review screen shows the runners-up so a person can see what it
    considered and not only what it chose.
    """
    body_norm = normalize(body)
    ranked = []
    for cand in candidates:
        hits: list[dict[str, Any]] = []
        spans: list[tuple[int, int]] = []
        for phrase in phrases_for(cand):
            pat = _pattern(phrase)
            if pat is None:
                continue
            found = [(m.start(), m.end()) for m in pat.finditer(body_norm)]
            if found:
                hits.append({"phrase": phrase, "count": len(found)})
                spans.extend(found)
        # DISTINCT mentions, not the sum of per-phrase counts. "1120
        # Jackson Street" matches the full address, the street line and
        # the derived "Jackson" -- one thing somebody said, counted three
        # times, which would let a property with more spellings of its own
        # name beat one that was actually discussed more.
        total = _distinct_spans(spans)
        hits.sort(key=lambda h: (-h["count"], h["phrase"]))
        ranked.append({
            "key": cand["key"],
            "label": cand.get("label") or cand["key"],
            "mentions": total,
            "phrases": hits,
            # Short aliases are the ones most likely to have matched
            # something unintended; surfaced so a reader can discount them.
            "weak": all(len(normalize(h["phrase"])) < SHORT_ALIAS_LEN
                        for h in hits) if hits else False,
        })
    ranked.sort(key=lambda r: (-r["mentions"], r["label"]))
    return ranked


def decide(ranked: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn a ranking into one of three outcomes, and never a guess."""
    scored = [r for r in ranked if r["mentions"] >= MIN_MENTIONS]

    if not scored:
        best = ranked[0] if ranked and ranked[0]["mentions"] else None
        return {
            "outcome": "unassigned",
            "key": None,
            "label": None,
            "candidates": ranked,
            "reason": (
                f"No property was mentioned at least {MIN_MENTIONS} times."
                + (f" The closest was {best['label']} with {best['mentions']}."
                   if best else " Nothing matched at all.")),
        }

    leader = scored[0]
    runner = scored[1] if len(scored) > 1 else None

    if runner and (leader["mentions"] - runner["mentions"]) < MARGIN:
        return {
            "outcome": "ambiguous",
            "key": None,
            "label": None,
            "candidates": ranked,
            "reason": (
                f"{leader['label']} was mentioned {leader['mentions']} times and "
                f"{runner['label']} {runner['mentions']} — too close to call. "
                f"A meeting can legitimately cover both."),
        }

    return {
        "outcome": "matched",
        "key": leader["key"],
        "label": leader["label"],
        "candidates": ranked,
        "reason": (
            f"{leader['label']} mentioned {leader['mentions']} times"
            + (f", next closest {runner['label']} with {runner['mentions']}"
               if runner else " and no other property mentioned")
            + "."),
    }


def match(body: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Score then decide. The one entry point callers need."""
    return decide(score(body, candidates))
