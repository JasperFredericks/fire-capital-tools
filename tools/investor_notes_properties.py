"""
FIRE Capital Tools - the property list a transcript can be matched to.

Assembled from all three places this app records a property, because no
one of them is complete:

  Deal Dive        two deals, address only, no name column at all
  Underwriting     property_label, free text
  Site DD          property_label, free text

The one property with a real building name -- Eagle Rock Apartments --
exists ONLY as an Underwriting label. It is not a Deal Dive record. So
matching against Deal Dive alone would make the property most likely to
be named out loud on a call the one property that could never be
matched.

KEYS

    deal:1                        a real Deal Dive record
    label:eagle rock apartments   known only by a label

A deal-backed property absorbs any label that normalises to its address,
so Site DD's "19 bay vista drive" and Deal Dive's "19 Bay Vista Drive"
are one property rather than two entries competing for the same
transcript.
"""

from __future__ import annotations

from typing import Any

from tools import investor_notes_match as matching

KEY_DEAL = "deal:"
KEY_LABEL = "label:"


def deal_key(deal_id: Any) -> str:
    return f"{KEY_DEAL}{deal_id}"


def label_key(label: str) -> str:
    return f"{KEY_LABEL}{matching.normalize(label)}"


def deal_label(deal: dict[str, Any]) -> str:
    """What to call a deal on screen. Deal Dive has no name column, so
    this is the address plus enough to disambiguate."""
    parts = [str(deal.get("address") or "").strip()]
    city = str(deal.get("city") or "").strip()
    state = str(deal.get("state") or "").strip()
    tail = " ".join(x for x in (city, state) if x)
    if tail:
        parts.append(tail)
    return ", ".join(p for p in parts if p) or f"Deal {deal.get('id')}"


def build(deals: list[dict[str, Any]],
          underwriting_labels: list[str],
          site_dd_labels: list[str],
          aliases: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """One entry per property, with every name it is known by.

    Deals come first and claim their normalised address; a label from
    another tool that matches an existing entry is folded in as an alias
    rather than creating a rival candidate.
    """
    aliases = aliases or {}
    entries: list[dict[str, Any]] = []
    by_norm: dict[str, dict[str, Any]] = {}
    by_deal: dict[Any, dict[str, Any]] = {}
    # Names contributed by records that named their deal, merged with the
    # stored aliases below rather than overwritten by them.
    folded: dict[str, list[str]] = {}

    for deal in deals or []:
        label = deal_label(deal)
        entry = {
            "key": deal_key(deal.get("id")),
            "label": label,
            "address": str(deal.get("address") or "").strip() or None,
            "deal_id": deal.get("id"),
            "sources": ["Deal Dive"],
            "aliases": [],
        }
        entries.append(entry)
        by_deal[deal.get("id")] = entry
        for form in (label, entry["address"]):
            norm = matching.normalize(form or "")
            if norm:
                by_norm.setdefault(norm, entry)

    def absorb(item: Any, source: str) -> None:
        """Fold one tool's record into the registry.

        `item` is a bare label, or a (label, deal_id) pair.

        A RECORD THAT NAMES ITS DEAL FOLDS INTO THAT DEAL, FULL STOP

        Absorption used to work only by normalising the label against a
        deal's address, which is why Site DD's "19 bay vista drive"
        merged with Deal Dive's record and "Nabob Hill" did not -- the
        latter is a building's name, not its address, so it spawned a
        rival entry for a property that already existed.

        That rivalry is not cosmetic. Adding "Nabob Hill" as an alias of
        deal:2 while a label:nabob hill entry still existed made BOTH
        claim the phrase, and the matcher -- correctly refusing to guess
        between two equal candidates -- then matched nothing at all. A
        transcript naming the property stopped being assignable.

        So an explicit deal_id wins over any name comparison. It is a
        person stating that these are the same property, which is better
        evidence than a string.
        """
        label, deal_id = item if isinstance(item, tuple) else (item, None)
        if deal_id is not None and deal_id in by_deal:
            existing = by_deal[deal_id]
            if source not in existing["sources"]:
                existing["sources"].append(source)
            # The local name is kept so a transcript that says "Nabob
            # Hill" reaches the deal it belongs to WITHOUT anyone having
            # to add an alias row by hand. Held separately because the
            # stored alias table is assigned wholesale further down.
            name = (label or "").strip()
            if name:
                folded.setdefault(existing["key"], [])
                if name not in folded[existing["key"]]:
                    folded[existing["key"]].append(name)
            return
        norm = matching.normalize(label or "")
        if not norm:
            return
        existing = by_norm.get(norm)
        if existing is not None:
            if source not in existing["sources"]:
                existing["sources"].append(source)
            return
        entry = {
            "key": label_key(label),
            "label": label.strip(),
            "address": None,
            "deal_id": None,
            "sources": [source],
            "aliases": [],
        }
        entries.append(entry)
        by_norm[norm] = entry

    for label in underwriting_labels or []:
        absorb(label, "Underwriting")
    for label in site_dd_labels or []:
        absorb(label, "Site DD")

    for entry in entries:
        entry["aliases"] = list(aliases.get(entry["key"], []))
        for name in folded.get(entry["key"], []):
            if name not in entry["aliases"] and name != entry["label"]:
                entry["aliases"].append(name)
        entry["has_deal"] = entry["deal_id"] is not None
        # Surfaced on the page: a property with no deal record and no
        # aliases is matchable only by its exact label, which is the
        # weakest possible position and worth saying out loud.
        entry["match_risk"] = (not entry["has_deal"]) and not entry["aliases"]

    entries.sort(key=lambda e: e["label"].lower())
    return entries


def find(entries: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for e in entries:
        if e["key"] == key:
            return e
    return None
