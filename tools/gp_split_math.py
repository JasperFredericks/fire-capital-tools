"""
Splitting the GP promote among named partners.

Strictly downstream of the cascade. The waterfall decides how much the GP
receives; this decides how that amount is divided between the people who
make up the GP. Nothing here can change what the GP is owed -- it only
divides a number the cascade has already fixed, which is why adding it
cannot move any existing figure.

    run_waterfall()   -> gp_received, gp_flows      (unchanged)
    allocate()        -> who gets which slice of it (new)

── Per period, then summed ──────────────────────────────────────────────

Each period's GP cents are split among the partners, and a partner's
total is the sum of their own period slices. The obvious alternative --
split the grand total, and separately split each period -- allows the two
to disagree by a cent or two once rounding is involved, leaving a report
whose columns do not add up to its own totals. Deriving the total from
the periods makes that impossible by construction rather than by check.

── Rounding ─────────────────────────────────────────────────────────────

split_pro_rata() from waterfall_math, unchanged: floor every share, hand
each leftover cent to the largest holder, ties broken by position. That
is already the convention for LP pro-rata, and a second rounding rule in
the same report would be a bug waiting to be discovered by whoever
reconciles the two.

── No partners configured ───────────────────────────────────────────────

The default is one implicit bucket holding 100%, named "GP". A scenario
with no partner rows therefore reports exactly what it reported before
this module existed -- same total, same single line -- and `is_default`
marks it so the UI can stay quiet rather than showing a one-row split
table that says nothing.
"""

from __future__ import annotations

from typing import Any

from tools.waterfall_math import split_pro_rata, to_cents, to_dollars

# A GP with more than this many partners is far more likely a data-entry
# accident than a real ownership structure.
MAX_PARTNERS = 20

# Shares are entered as percentages and must account for the whole of the
# promote. Compared in cents (100.00% -> 10000) so the check is exact
# rather than a float tolerance.
REQUIRED_SHARE_TOTAL_CENTS = to_cents(100.0)

DEFAULT_PARTNER_NAME = "GP"


class GPSplitError(ValueError):
    """The partner set cannot be allocated as entered."""


class GPSplitInvariantError(AssertionError):
    """Partner allocations did not conserve the GP's cash. Raised, never
    returned: a split that loses or invents a cent must not render."""


def normalize(partners: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Partner rows -> the list allocate() works on.

    Rows with no share at all are dropped, so a half-filled "add partner"
    row does not silently book a 0% partner into the report.
    """
    out = []
    for idx, p in enumerate(partners or []):
        share = p.get("share_pct")
        if share is None or share == "":
            continue
        try:
            share = float(share)
        except (TypeError, ValueError):
            raise GPSplitError(
                f"{p.get('name') or f'Partner {idx + 1}'}: share must be a number.")
        out.append({
            "id": p.get("id"),
            "investor_id": p.get("investor_id"),
            "name": (str(p.get("name") or "").strip()
                     or f"Partner {idx + 1}"),
            "share_pct": share,
            "notes": p.get("notes"),
            "sort_order": p.get("sort_order", idx),
        })
    out.sort(key=lambda p: (p["sort_order"], p["name"]))
    return out


def validate(partners: list[dict[str, Any]]) -> None:
    """Reject a partner set that cannot be a complete ownership split.

    Shares must total exactly 100%. Deliberately an error rather than a
    silent renormalization: a set summing to 90% means a partner is
    missing, and quietly scaling the remaining nine up to cover it would
    pay each of them more than their agreement says.
    """
    if not partners:
        return
    if len(partners) > MAX_PARTNERS:
        raise GPSplitError(f"A GP can have at most {MAX_PARTNERS} partners.")

    for p in partners:
        if p["share_pct"] < 0:
            raise GPSplitError(f"{p['name']}: share cannot be negative.")
        if p["share_pct"] > 100:
            raise GPSplitError(f"{p['name']}: share cannot exceed 100%.")

    total = sum(to_cents(p["share_pct"]) for p in partners)
    if total != REQUIRED_SHARE_TOTAL_CENTS:
        raise GPSplitError(
            f"Partner shares must total exactly 100% — they currently total "
            f"{total / 100.0:g}%.")


def default_partners() -> list[dict[str, Any]]:
    """The implicit single bucket used when nothing is configured."""
    return [{"id": None, "investor_id": None, "name": DEFAULT_PARTNER_NAME,
             "share_pct": 100.0, "notes": None, "sort_order": 0}]


def allocate(result: dict[str, Any],
             partners: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Divide a completed waterfall's GP promote among its partners.

    `result` is run_waterfall()'s output, read only -- its cent-exact
    `_cents` figures are the source, so the split works on the same
    numbers the invariants do rather than on rounded dollars.

    Returns the split plus the per-period detail, and raises if the
    allocation fails to conserve the GP's cash to the cent.
    """
    normalized = normalize(partners)
    is_default = not normalized
    if is_default:
        normalized = default_partners()
    validate(normalized)

    cents = result["_cents"]
    gp_received = cents["gp_received"]
    period_rows = cents["period_rows"]
    weights = [to_cents(p["share_pct"]) for p in normalized]

    # Period by period; a partner's total is the sum of their own slices.
    per_period = []
    totals = [0] * len(normalized)
    for row in period_rows:
        shares = split_pro_rata(row["gp"], weights)
        for i, s in enumerate(shares):
            totals[i] += s
        per_period.append({
            "year": row["year"],
            "gp": to_dollars(row["gp"]),
            "gp_cents": row["gp"],
            "shares": [to_dollars(s) for s in shares],
            "shares_cents": list(shares),
        })

    # The GP's own cash-flow vector, split the same way, so a partner's
    # flows are reportable without a second allocation pass.
    partner_flows = [[0] for _ in normalized]
    for row in per_period:
        for i, s in enumerate(row["shares_cents"]):
            partner_flows[i].append(s)

    out = {
        "is_default": is_default,
        "partner_count": len(normalized),
        "gp_distributed": to_dollars(gp_received),
        "gp_distributed_cents": gp_received,
        "partners": [{
            "id": p["id"],
            "investor_id": p["investor_id"],
            "name": p["name"],
            "share_pct": p["share_pct"],
            "notes": p["notes"],
            "distributed": to_dollars(totals[i]),
            "distributed_cents": totals[i],
            "cashflows": [to_dollars(x) for x in partner_flows[i]],
        } for i, p in enumerate(normalized)],
        "periods": per_period,
        "_cents": {"gp_received": gp_received, "totals": totals,
                   "period_rows": per_period},
    }
    out["invariant_checks"] = check_invariants(out)
    return out


def check_invariants(split: dict[str, Any]) -> list[dict[str, Any]]:
    """Invariant 11: the partner split conserves the GP's cash exactly.

    Numbered on from waterfall_math's ten deliberately -- this is the same
    conservation discipline applied one level down, and it is asserted on
    every allocation rather than offered as a diagnostic. Raises on the
    first failure: a split that does not add up has no correct number in
    it worth displaying.
    """
    c = split["_cents"]
    checks: list[dict[str, Any]] = []

    def ok(n, name, passed, detail=""):
        checks.append({"n": n, "name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            raise GPSplitInvariantError(f"Invariant {n} ({name}) failed: {detail}")

    # 11a. every period: the partners' slices are exactly that period's GP
    for row in c["period_rows"]:
        allocated = sum(row["shares_cents"])
        if allocated != row["gp_cents"]:
            ok(11, "partner split equals GP cash in every period", False,
               f"year {row['year']}: partners got {allocated} of {row['gp_cents']}")
    ok(11, "partner split equals GP cash in every period", True)

    # 11b. cumulatively: totals reconcile to gp_received
    total = sum(c["totals"])
    ok(11, "partner totals equal total GP promote",
       total == c["gp_received"],
       f"partners total {total} != GP received {c['gp_received']}")

    # 11c. no partner receives a negative allocation
    for i, t in enumerate(c["totals"]):
        if t < 0:
            ok(11, "no partner allocation is negative", False, f"partner index {i}")
    ok(11, "no partner allocation is negative", True)
    return checks
