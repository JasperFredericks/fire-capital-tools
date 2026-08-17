"""
FIRE Capital Tools - synthesising transcripts into an investor update.

Prompt construction, response parsing and the cache key. The OpenAI call
itself is one small function at the bottom; everything above it is pure
and testable without a network or a key.

THIS IS MULTI-DOCUMENT SYNTHESIS, NOT SUMMARISATION

Several meetings, each contributing to different sections. A quarter's
operations discussion might be spread over three calls and a capital
item mentioned once in passing. So the model is given every transcript
with an explicit id and date, and asked to place claims under headings
while naming which meeting each came from.

EVERY CLAIM CARRIES ITS SOURCE

Each bullet records the transcript it came from. Michelle should be able
to point at any sentence in an update and get to the meeting and date
behind it -- and if she cannot, the sentence should not be there.
parse_response drops any bullet whose source id is not in the set that
was actually sent, so the model cannot attribute a claim to a meeting
that was not part of the query.

AN EMPTY SECTION SAYS SO

Padding a section nobody discussed is how a document stops being worth
reading. A section with no source material renders as an explicit "not
discussed in these meetings" rather than a plausible paragraph.

FINANCIAL FIGURES ARE NARRATIVE AND GO NOWHERE

A number said on a call is hearsay relative to the underwriting model.
The financial section is prose only; nothing here writes to Underwriting,
Deal Dive or any other tool, and no caller is given a machine-readable
figure to write with. Where a transcript states a number the model also
holds, the page shows both and flags the difference -- see
compare_with_model.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Bumped when the prompt or the expected shape changes, so a cached
# update generated under the old wording is not served as if it were
# produced by the current one.
PROMPT_VERSION = "investor_update_v2"

SECTIONS: tuple[dict[str, str], ...] = (
    {"key": "operations",
     "name": "Operations",
     "brief": "occupancy, leasing, turnovers, staffing, resident issues, "
              "day-to-day management"},
    {"key": "capital_improvements",
     "name": "CapEx Update",
     "brief": "renovation and capex work: planned, underway or completed"},
    {"key": "financial_update",
     "name": "Financial Update",
     "brief": "revenue, expenses, budget and distributions AS DISCUSSED. "
              "Narrative only — never presented as authoritative figures"},
    {"key": "market_update",
     "name": "Market Update",
     "brief": "submarket conditions, comparable properties, competitive "
              "supply, rents in the area"},
    {"key": "community_events",
     "name": "Community Events",
     "brief": "resident events, community building, engagement"},
    {"key": "next_steps",
     "name": "Next Steps",
     "brief": "what happens next and who is doing it: decisions taken, "
              "actions agreed, dates committed to. Only what was actually "
              "said -- never a plan the model thinks would be sensible"},
)

SECTION_KEYS = tuple(s["key"] for s in SECTIONS)
SECTION_NAMES = {s["key"]: s["name"] for s in SECTIONS}

EMPTY_SECTION_TEXT = "Not discussed in the meetings covering this period."

# Guard rails on what is sent. A quarter of meetings is well inside this;
# the cap exists so a mistaken selection cannot produce a surprise bill.
MAX_TRANSCRIPTS = 24
MAX_CHARS_PER_TRANSCRIPT = 60_000
MAX_TOTAL_CHARS = 400_000


class TooMuchInput(Exception):
    """Raised with a message written for the person who selected them."""


def cache_key(property_key: str, start: str, end: str,
              transcript_ids: list[int], prompt_version: str = PROMPT_VERSION,
              model: str | None = None) -> str:
    """Identity of one synthesis query.

    The exact set of transcript ids is in the key, not just the date
    range -- so uploading another meeting inside a range already
    generated correctly invalidates, while re-opening the same update is
    free. Sorted so selection order cannot produce two keys for one
    query.
    """
    payload = json.dumps({
        "property": property_key,
        "start": start,
        "end": end,
        "transcripts": sorted(int(t) for t in transcript_ids),
        "prompt": prompt_version,
        "model": model or "",
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_size(transcripts: list[dict[str, Any]]) -> None:
    if not transcripts:
        raise TooMuchInput("Select at least one transcript.")
    if len(transcripts) > MAX_TRANSCRIPTS:
        raise TooMuchInput(
            f"{len(transcripts)} transcripts selected — the limit is "
            f"{MAX_TRANSCRIPTS} in one update. Narrow the date range.")
    total = sum(len(t.get("body") or "") for t in transcripts)
    if total > MAX_TOTAL_CHARS:
        raise TooMuchInput(
            f"Those transcripts total {total:,} characters — the limit is "
            f"{MAX_TOTAL_CHARS:,}. Narrow the date range or deselect the "
            f"longest meetings.")


def _clip(body: str) -> str:
    body = (body or "").strip()
    if len(body) <= MAX_CHARS_PER_TRANSCRIPT:
        return body
    # Visible, so a truncated meeting is never mistaken for a short one.
    return body[:MAX_CHARS_PER_TRANSCRIPT] + "\n[transcript truncated]"


def build_instructions() -> str:
    lines = [
        "You write quarterly investor updates for a multifamily real estate "
        "sponsor, from the transcripts of that quarter's meetings.",
        "",
        "Place what was actually discussed under these headings:",
    ]
    for s in SECTIONS:
        lines.append(f"  - {s['name']} ({s['key']}): {s['brief']}")
    lines += [
        "",
        "RULES, all of which matter more than producing a full-looking document:",
        "1. Every bullet must carry the transcript_id it came from. A claim you "
        "cannot attribute to a specific transcript must be left out.",
        "2. Do not infer, extrapolate or fill gaps. Report what was said.",
        "3. If a section was not discussed, return an EMPTY list of points for "
        "it. Do not pad it. An empty section is a useful fact.",
        "4. Financial figures are reported as narrative only, as things people "
        "said, never as authoritative accounting. Attribute them to the "
        "speaker or meeting.",
        "5. Where two meetings conflict, say so and cite both rather than "
        "silently preferring the later one.",
        "6. Quote or closely paraphrase. Do not editorialise or add optimism "
        "that was not in the transcript.",
        "",
        "Return JSON only, matching this shape exactly:",
        '{"sections": [{"key": "operations", "points": '
        '[{"text": "...", "transcript_id": 12}]}]}',
    ]
    return "\n".join(lines)


def build_input(transcripts: list[dict[str, Any]], property_label: str,
                start: str, end: str) -> str:
    parts = [
        f"Property: {property_label}",
        f"Period: {start} to {end}",
        f"Meetings: {len(transcripts)}",
        "",
    ]
    for t in transcripts:
        parts += [
            f"--- transcript_id {t['id']} | {t.get('transcript_date')} | "
            f"{t.get('title') or t.get('original_name') or 'Untitled'} ---",
            _clip(t.get("body") or ""),
            "",
        ]
    return "\n".join(parts)


def _extract_json(text: str) -> dict[str, Any]:
    """Parse the model's reply, tolerating a fenced block around it."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except (TypeError, ValueError):
                pass
    return {}


def parse_response(text: str, transcripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn the reply into the five sections, attributed and verified.

    Every section is present in the output whether or not the model
    returned it, in the fixed order above -- so a section the model
    forgot reads as "not discussed" rather than vanishing from the
    document.

    A point whose transcript_id is not in the set that was sent is
    DROPPED. The model attributing a claim to a meeting that was not part
    of the query is exactly the failure the attribution is there to
    prevent, so it is not repaired or guessed at.
    """
    by_id = {int(t["id"]): t for t in transcripts}
    parsed = _extract_json(text)
    raw_sections = {}
    for entry in (parsed.get("sections") or []):
        key = str(entry.get("key") or "").strip()
        if key in SECTION_KEYS:
            raw_sections[key] = entry.get("points") or []

    out = []
    for spec in SECTIONS:
        points = []
        for point in raw_sections.get(spec["key"], []):
            body = " ".join(str(point.get("text") or "").split())
            if not body:
                continue
            try:
                tid = int(point.get("transcript_id"))
            except (TypeError, ValueError):
                continue
            source = by_id.get(tid)
            if source is None:
                continue          # unattributable: dropped, not repaired
            points.append({
                "text": body,
                "transcript_id": tid,
                "date": source.get("transcript_date"),
                "title": (source.get("title") or source.get("original_name")
                          or "Untitled"),
            })
        out.append({
            "key": spec["key"],
            "name": spec["name"],
            "points": points,
            "empty": not points,
            "empty_text": EMPTY_SECTION_TEXT,
        })
    return out


def dropped_count(text: str, transcripts: list[dict[str, Any]]) -> int:
    """How many points were discarded as unattributable. Shown on the
    page: silently dropping model output would hide a real problem."""
    by_id = {int(t["id"]) for t in transcripts}
    parsed = _extract_json(text)
    dropped = 0
    for entry in (parsed.get("sections") or []):
        for point in (entry.get("points") or []):
            try:
                tid = int(point.get("transcript_id"))
            except (TypeError, ValueError):
                dropped += 1
                continue
            if tid not in by_id:
                dropped += 1
    return dropped


# ── Divergence against what the tools already hold ───────────────────────

_MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def figures_in(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dollar amounts mentioned in the narrative, with their source.

    Extracted for DISPLAY beside the model's own figures, never to write
    anywhere. A number on a call is what somebody said; a number in
    Underwriting is what was modelled, and the two disagreeing is
    information rather than an error to resolve automatically.
    """
    found = []
    for section in sections:
        for point in section.get("points", []):
            for raw in _MONEY.findall(point["text"]):
                try:
                    value = float(raw.replace(",", ""))
                except ValueError:
                    continue
                found.append({
                    "value": value,
                    "section": section["name"],
                    "text": point["text"],
                    "transcript_id": point["transcript_id"],
                    "date": point.get("date"),
                })
    return found


def compare_with_model(sections: list[dict[str, Any]],
                       model_figures: dict[str, float] | None) -> list[dict[str, Any]]:
    """Pair spoken figures with modelled ones and flag the gaps.

    Returns rows for a table, never an instruction. Nothing in this
    module writes to another tool, and this function deliberately returns
    no "correct" value -- only the two numbers and the difference, for a
    person to look at.
    """
    if not model_figures:
        return []
    rows = []
    for spoken in figures_in(sections):
        for name, modelled in model_figures.items():
            if not modelled:
                continue
            delta = spoken["value"] - modelled
            if abs(delta) / max(abs(modelled), 1.0) <= 0.02:
                continue          # within 2%: not a divergence worth raising
            rows.append({
                "figure": name,
                "spoken": spoken["value"],
                "modelled": modelled,
                "delta": delta,
                "section": spoken["section"],
                "transcript_id": spoken["transcript_id"],
                "date": spoken["date"],
                "context": spoken["text"],
            })
    return rows


# ── The one impure function ──────────────────────────────────────────────

def synthesize(*, api_key: str, model_name: str, transcripts: list[dict[str, Any]],
               property_label: str, start: str, end: str) -> dict[str, Any]:
    """Call the model once and return parsed sections plus the raw reply.

    Records against the shared OpenAI counter as 'investor_notetaker',
    immediately after the call returns -- so a cached update, which never
    reaches this function, cannot inflate the count.
    """
    from openai import OpenAI

    from tools import openai_usage

    check_size(transcripts)
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model_name,
        input=[
            {"role": "system",
             "content": [{"type": "input_text", "text": build_instructions()}]},
            {"role": "user",
             "content": [{"type": "input_text",
                          "text": build_input(transcripts, property_label,
                                              start, end)}]},
        ],
    )
    openai_usage.record(openai_usage.FEATURE_INVESTOR_NOTETAKER, response)

    text = getattr(response, "output_text", "") or ""
    return {
        "sections": parse_response(text, transcripts),
        "dropped": dropped_count(text, transcripts),
        "model": model_name,
        "raw": text,
    }
