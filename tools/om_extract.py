"""
FIRE Capital Tools - offering memorandum extraction.

An OM is the seller's document. It is marketing, and it is the only place
some facts about a deal appear. Both of those are true at once, which is
what this module is shaped around: it reproduces what the OM says, with a
page number beside every number, and it never computes anything.

WHAT THIS PRODUCES, AND WHAT IT REFUSES TO PRODUCE

    property           facts as printed -- name, address, units, vintage
    asking_terms       price, cap rate, financing as offered
    stated_numbers[]   every number the OM prints, with the page it is on
    pitch[]            the seller's argument, QUOTED, never paraphrased
    not_stated[]       what an OM should contain and this one does not
    unreadable_pages[] pages that yielded no text

`not_stated` is a first-class output rather than an omission, because the
absence of a number in an OM is itself information -- an OM with no T12
reference and no expense detail is a different document from one that has
them, and a summary that simply lacked those lines would read as though
the question had never been asked.

THE GUARDS ARE STRUCTURAL, NOT PROMPTED

Instructions ask a model to behave. They do not make it. So every claim
this module returns is checked against the extracted text after the fact,
the same discipline as fire_metrics_ai_summary's validate_summary_with_
facts(): the prompt is the first line and the validator is the one that
decides.

    every number anywhere in the output must appear in the source
    every stated number must appear on the page it cites
    every pitch bullet must appear in the source as written
    a not_stated entry may never contain a number

The first of those is the analogue of contains_unapproved_numbers(), and
is the one that matters most. A cap rate the OM never printed -- computed
from a price and an NOI that it did print -- is the most plausible thing
a model could produce here and the most damaging, because it would look
exactly like a quoted fact in a document Michelle reads as source
material. It cannot survive that check.

WHITESPACE

PDF text extraction inserts spaces inside words: a real production PDF
measured during design produced "Payroll T axes" for "Payroll Taxes".
That is a kerning artifact, not corruption -- the same file's numbers
extracted intact, with zero replacement characters. Quote matching is
therefore whitespace-normalised, or a correctly quoted bullet would fail
its own guard. Number matching tolerates internal whitespace for the same
reason and nothing else: a fabricated figure matches under neither rule.

WHAT IS NOT HERE

No arithmetic, no auto-population, no writes to a scenario. The summary
is reference material displayed beside a scenario and is never a data
source for one. tests/test_om_extract.py asserts that structurally rather
than trusting this paragraph.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

PROMPT_VERSION = "om_extraction_v1"
SCHEMA_NAME = "om_extraction"

# Pages beyond this are not sent. Sized in design against a real
# production PDF at 531 tokens/page for dense financials: 40 pages is
# ~22,000 prompt tokens worst case, about 2.5x the largest real call this
# app has made. Over the cap the first 40 are read and the rest are named.
PAGE_CAP = 40

# A page with less than this much text is treated as having none. A
# scanned page often yields a handful of stray characters from a logo or
# a header rather than a clean zero.
MIN_CHARS_PER_PAGE = 20

# Below this share of readable pages the document is refused outright.
# A mostly-image OM produces a summary built from captions.
MIN_READABLE_SHARE = 0.30

# Token estimate for the confirm-before-spend gate. Measured, not
# guessed: 4.8 characters per token on real OM-like text.
CHARS_PER_TOKEN = 4.8
INSTRUCTION_TOKENS = 900
COMPLETION_TOKEN_ALLOWANCE = 2000

# Numbers, as they appear in a document: 1,343,580 / $6,990,000 / 5.50%
#
# The boundaries matter more than the digits. Without them "T12" reads as
# the number 12, and "No T12 is referenced" -- a correct and expected
# 'not stated' entry -- gets rejected for containing a figure. Digits
# bounded by letters are part of a word, not a quantity: T12, Q4, 2BR,
# A1. Only free-standing runs are treated as numbers to be checked.
NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*(?:\.\d+)?(?![A-Za-z])")


class OMUnreadable(Exception):
    """The PDF carries no text this tool can read.

    Raised before any API call. A scanned OM is the case this exists for:
    it is detectable locally, costs nothing to detect, and producing a
    summary from it would mean summarising nothing.
    """


class OMRejected(Exception):
    """The model returned something that failed a guard."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


# ── Reading the file ─────────────────────────────────────────────────────

def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_key(sha256: str, prompt_version: str = PROMPT_VERSION) -> str:
    """What makes two extractions the same extraction.

    The file's bytes and the prompt that read them. Bumping
    PROMPT_VERSION invalidates every stored summary, which is the point:
    a changed prompt produces a different document and serving the old
    one under the new version would misattribute it.
    """
    return f"{prompt_version}:{sha256}"


def read_pages(data: bytes) -> list[str]:
    """The text of every page, in order, one entry per page.

    Page boundaries are preserved because the whole design rests on them:
    a number without the page it came from cannot be checked against the
    source, and checking against the source is the point.
    """
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:                      # noqa: BLE001
            raise OMUnreadable(
                "This PDF is password-protected, so its text cannot be "
                "read. Save an unprotected copy and upload that."
            ) from exc
    return [(page.extract_text() or "") for page in reader.pages]


def readable_pages(pages: list[str]) -> list[int]:
    """1-based numbers of the pages that actually carry text."""
    return [i for i, text in enumerate(pages, start=1)
            if len(text.strip()) >= MIN_CHARS_PER_PAGE]


def inspect(data: bytes) -> dict[str, Any]:
    """Everything the confirm gate needs, computed locally for free.

    Nothing in here reaches the network. The page count, the readability
    verdict and the cost estimate are all derived from the file itself,
    so a document that cannot be used is refused before it can spend
    anything.
    """
    pages = read_pages(data)
    if not pages:
        raise OMUnreadable("This PDF has no pages.")

    readable = readable_pages(pages)
    share = len(readable) / len(pages)
    if not readable:
        raise OMUnreadable(
            f"No text could be read from any of this PDF's {len(pages)} "
            "pages. This is usually a scanned document — an image of a "
            "page rather than a page. Nothing was sent and nothing was "
            "charged. Ask the broker for a text PDF."
        )
    if share < MIN_READABLE_SHARE:
        raise OMUnreadable(
            f"Only {len(readable)} of {len(pages)} pages carry readable "
            f"text ({share * 100:.0f}%). The rest appear to be scanned "
            "images, so a summary would be built from a fraction of the "
            "document. Nothing was sent and nothing was charged."
        )

    used = list(range(1, min(len(pages), PAGE_CAP) + 1))
    skipped = list(range(PAGE_CAP + 1, len(pages) + 1))
    chars = sum(len(pages[i - 1]) for i in used)
    prompt_tokens = int(chars / CHARS_PER_TOKEN) + INSTRUCTION_TOKENS

    return {
        "page_count": len(pages),
        "readable_pages": readable,
        "unreadable_pages": [i for i in range(1, len(pages) + 1)
                             if i not in set(readable)],
        "readable_share": share,
        "pages_used": used,
        "pages_skipped": skipped,
        "over_cap": bool(skipped),
        "page_cap": PAGE_CAP,
        "characters": chars,
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_completion_tokens": COMPLETION_TOKEN_ALLOWANCE,
        "pages": pages,
    }


def skipped_note(inspection: dict[str, Any]) -> str:
    """Said plainly, or not at all. Never a silent truncation."""
    skipped = inspection.get("pages_skipped") or []
    if not skipped:
        return ""
    return (f"This OM has {inspection['page_count']} pages and the first "
            f"{inspection['page_cap']} were read. Pages "
            f"{skipped[0]}–{skipped[-1]} were not sent and are not "
            f"reflected anywhere in this summary.")


# ── The request ──────────────────────────────────────────────────────────

def build_instructions() -> str:
    return (
        "You are reading an offering memorandum and reproducing what it "
        "says. You are not analysing it, valuing it, or checking it. "
        "Return valid JSON matching the schema. "
        "Every number you report must be copied from the document exactly "
        "as printed, including its currency symbol, commas, percent sign "
        "and decimal places. "
        "Never calculate anything. Do not compute a cap rate, a price per "
        "unit, a per-square-foot figure, a total, a difference or a "
        "percentage that the document does not itself print. If the "
        "document prints a price and an income but not a cap rate, there "
        "is no cap rate. "
        "For every number, give the page it appears on, counting from 1. "
        "In 'pitch', quote the seller's own sentences verbatim, between 3 "
        "and 5 of them, each with its page. Do not paraphrase, summarise, "
        "soften or improve them — they are quotations. "
        "In 'not_stated', list what an offering memorandum would normally "
        "contain that this one does not: named absences such as a T12 "
        "reference, an itemised expense breakdown, a rent roll, financing "
        "terms, or a year built. Never put a number in 'not_stated'. "
        "In 'property' and 'asking_terms' record only what is printed. "
        "Use an empty string for anything the document does not state; "
        "never infer, estimate or fill a gap. "
        "If the text is fragmentary, report less rather than guessing."
    )


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "property": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "address": {"type": "string"},
                "unit_count": {"type": "string"},
                "year_built": {"type": "string"},
                "property_type": {"type": "string"},
                "unit_mix": {"type": "string"},
            },
            "required": ["name", "address", "unit_count", "year_built",
                         "property_type", "unit_mix"],
            "additionalProperties": False,
        },
        "asking_terms": {
            "type": "object",
            "properties": {
                "asking_price": {"type": "string"},
                "cap_rate": {"type": "string"},
                "price_per_unit": {"type": "string"},
                "financing": {"type": "string"},
                "guidance": {"type": "string"},
            },
            "required": ["asking_price", "cap_rate", "price_per_unit",
                         "financing", "guidance"],
            "additionalProperties": False,
        },
        "stated_numbers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value_as_written": {"type": "string"},
                    "page": {"type": "integer"},
                },
                "required": ["label", "value_as_written", "page"],
                "additionalProperties": False,
            },
        },
        "pitch": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "page": {"type": "integer"},
                },
                "required": ["quote", "page"],
                "additionalProperties": False,
            },
        },
        "not_stated": {"type": "array", "items": {"type": "string"}},
        "unreadable_pages": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["property", "asking_terms", "stated_numbers", "pitch",
                 "not_stated", "unreadable_pages"],
    "additionalProperties": False,
}


def build_input(pages: list[str], pages_used: list[int]) -> str:
    """The document, page-labelled so a citation can be checked."""
    blocks = [f"--- PAGE {n} ---\n{pages[n - 1].strip()}" for n in pages_used]
    return "\n\n".join(blocks)


# ── The guards ───────────────────────────────────────────────────────────

def normalize_space(text: str) -> str:
    """Collapse all whitespace. See the module note on 'Payroll T axes'."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _appears(needle: str, haystack: str) -> bool:
    """Verbatim, then whitespace-tolerant.

    The second form exists only for the extraction artifact and does not
    loosen the guard in any way that matters: a number the document never
    printed matches under neither.
    """
    needle = (needle or "").strip()
    if not needle:
        return False
    if needle in haystack:
        return True
    return normalize_space(needle) in normalize_space(haystack)


def number_tokens(text: str) -> list[str]:
    return NUMBER_TOKEN_RE.findall(text or "")


def _strings_in(value: Any, skip_keys: tuple[str, ...] = ("page",)) -> list[str]:
    """Every string anywhere in the parsed output.

    Integers are skipped deliberately: `page` and `unreadable_pages` are
    page numbers this module supplied or can check directly, and feeding
    them to the number guard would ask the document to contain its own
    pagination.
    """
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in skip_keys:
                continue
            out.extend(_strings_in(item, skip_keys))
    elif isinstance(value, list):
        for item in value:
            out.extend(_strings_in(item, skip_keys))
    return out


def unapproved_numbers(parsed: dict[str, Any], pages: list[str]) -> list[str]:
    """Numbers in the output that the document does not contain.

    The analogue of fire_metrics_ai_summary.contains_unapproved_numbers(),
    with the source text standing in for the approved facts. This is the
    guard that catches derived arithmetic: a cap rate computed from a
    price and an income the OM did print is not itself in the document,
    so it has nowhere to match.
    """
    source = "\n".join(pages)
    source_norm = normalize_space(source)
    bad = []
    for text in _strings_in(parsed):
        for token in number_tokens(text):
            if token in source or token in source_norm:
                continue
            bad.append(token)
    return sorted(set(bad))


def validate(parsed: dict[str, Any], pages: list[str],
             pages_used: list[int]) -> list[str]:
    """Every reason this extraction cannot be shown. Empty means clean."""
    reasons: list[str] = []
    used = set(pages_used)

    # 1. Nothing numeric may exist that the document does not.
    stray = unapproved_numbers(parsed, pages)
    if stray:
        reasons.append(
            "these numbers do not appear anywhere in the document: "
            + ", ".join(stray[:8]))

    # 2. Every stated number must be on the page it cites.
    for item in parsed.get("stated_numbers") or []:
        page = item.get("page")
        value = item.get("value_as_written") or ""
        label = item.get("label") or "?"
        if not isinstance(page, int) or page not in used:
            reasons.append(f"{label!r} cites page {page}, which was not read")
            continue
        if not _appears(value, pages[page - 1]):
            reasons.append(
                f"{label!r} reports {value!r} as being on page {page}, "
                "but that page does not contain it")

    # 3. Every pitch bullet must be the document's own words.
    pitch = parsed.get("pitch") or []
    if not 3 <= len(pitch) <= 5:
        reasons.append(f"pitch has {len(pitch)} bullets; it must have 3 to 5")
    for item in pitch:
        quote = item.get("quote") or ""
        page = item.get("page")
        if isinstance(page, int) and page in used:
            haystack = pages[page - 1]
        else:
            haystack = "\n".join(pages)
        if not _appears(quote, haystack):
            reasons.append(
                f"pitch quote is not in the document as written: "
                f"{normalize_space(quote)[:70]!r}")

    # 4. An absence cannot carry a figure.
    for entry in parsed.get("not_stated") or []:
        if number_tokens(entry):
            reasons.append(
                f"'not stated' entry contains a number, which it may never "
                f"do: {normalize_space(entry)[:70]!r}")

    return reasons


# ── The call ─────────────────────────────────────────────────────────────

def extract(data: bytes, *, api_key: str, model_name: str,
            inspection: dict[str, Any] | None = None) -> dict[str, Any]:
    """One OM, one API call, guarded on the way out.

    The usage counter is written immediately after the call returns and
    before the response is parsed, so a call that came back malformed --
    and was still billed -- is still counted. Counting only successes
    would understate spend in exactly the situation where spend is being
    investigated.
    """
    from openai import OpenAI

    from tools import openai_usage

    inspection = inspection or inspect(data)
    pages, pages_used = inspection["pages"], inspection["pages_used"]

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model_name,
        input=[
            {"role": "system",
             "content": [{"type": "input_text", "text": build_instructions()}]},
            {"role": "user",
             "content": [{"type": "input_text",
                          "text": build_input(pages, pages_used)}]},
        ],
        text={"format": {"type": "json_schema", "name": SCHEMA_NAME,
                         "schema": SCHEMA, "strict": True}},
    )
    openai_usage.record(openai_usage.FEATURE_OM_EXTRACTION, response)

    parsed = json.loads(getattr(response, "output_text", "") or "{}")
    reasons = validate(parsed, pages, pages_used)
    if reasons:
        raise OMRejected(reasons)

    prompt_tokens, completion_tokens = openai_usage.tokens_from_response(response)
    return {
        "summary": parsed,
        "model": model_name,
        "prompt_version": PROMPT_VERSION,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "pages_used": pages_used,
        "pages_skipped": inspection["pages_skipped"],
        "skipped_note": skipped_note(inspection),
    }
