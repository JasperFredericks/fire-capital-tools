"""
FIRE Capital Tools - API & service cost reference.

A static inventory of every external service this app depends on, plus
what each one costs. Deliberately a config module rather than a database:
pricing changes maybe twice a year, so a tenth SQLite file (with its own
env var, its own live-path verification and its own delete cascade) would
be more moving parts than the problem has. Editing a figure here is a
commit, which means the change gets reviewed and keeps its history --
which is the right property for financial reference data.

There is therefore NO persistent storage behind this page, and the
standing "new storage must use the env-var-with-fallback pattern"
requirement is not applicable to it. The page says so itself rather than
leaving a reader to infer it.

Two rules govern the data below, both about not overstating what is known:

1. A figure that has not actually been confirmed is TBD, never a
   plausible-looking number. A guessed dollar amount on a costs page is
   worse than a blank, because it will be believed and then budgeted
   against. Every TBD names who can resolve it.

2. Live counters and stated figures are different kinds of claim and are
   marked as such (`live_counter`). RentCast and Google Places are read
   from the real usage tables at request time; everything else is a
   number a human typed on `last_verified` and has been decaying ever
   since. The template renders the two differently so they can never be
   read as equally current.

Usage is measurable for exactly two services. Google Maps JS bills on
client-side map loads the server never sees, and OpenAI has no local
counter (adding one is a deliberate future decision, not an oversight).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Every unconfirmed figure uses this exact string, so the page can style
# them uniformly and a reader can scan for what still needs an answer.
TBD = "TBD — confirm with Jasper"


@dataclass(frozen=True)
class Service:
    """One external dependency and what it costs.

    `configured_key` names the environment variable whose presence decides
    the configured/not-configured badge. It is only ever tested for
    presence -- the value is never read into the page, logged, or
    rendered.

    `live_counter` is the key into the live-usage dict the route builds
    ("rentcast" / "google_places"), or None for a service whose usage
    cannot be measured from here. A None here is a factual claim about
    measurability, not a to-do.
    """

    key: str
    name: str
    purpose: str
    used_by: tuple[str, ...]
    pricing_model: str
    plan: str
    monthly_cost: str
    last_verified: str
    notes: str
    configured_key: Optional[str] = None
    live_counter: Optional[str] = None
    free: bool = False

    @property
    def is_tbd(self) -> bool:
        return self.monthly_cost == TBD


# Date the static figures below were last checked against the vendors'
# own pricing pages. Bump this when you revisit them, even if nothing
# changed -- "checked and unchanged" is information too.
LAST_REVIEWED = "2026-08-10"


SERVICES: tuple[Service, ...] = (
    Service(
        key="rentcast",
        name="RentCast",
        purpose="Rent estimates, rental comparables and subject-property attributes.",
        used_by=("Rent Comps", "Deal Dive"),
        pricing_model="Free tier of 50 requests/month, then a per-request overage charge.",
        plan="Free tier (assumed — see notes)",
        monthly_cost="$0 while under the free tier",
        last_verified=LAST_REVIEWED,
        notes=(
            "The 50/month figure is hardcoded as the cap this app enforces, and the "
            "safety threshold is set below it at 45 so a lookup is refused before the "
            "real ceiling is reached. Whether the account is still on the free tier is "
            f"{TBD} — if it has been upgraded, the cap enforced here is stale and "
            "unnecessarily restrictive."
        ),
        configured_key="RENTCAST_API_KEY",
        live_counter="rentcast",
    ),
    Service(
        key="google_places",
        name="Google Places",
        purpose="Property ratings, review counts and review snippets.",
        used_by=("Deal Dive",),
        pricing_model="Per request, with a free monthly event allowance that varies by SKU tier.",
        plan="Pay-as-you-go with free allowance",
        monthly_cost=TBD,
        last_verified=LAST_REVIEWED,
        notes=(
            "The ~1,000 free events/month this app's cap is derived from is a "
            "researched estimate, NOT a confirmed figure — Google does not publish the "
            "per-tier number in one place, and the calls made here request Atmosphere "
            "fields that bill on top of the base request. The 100/month threshold is "
            "roughly 10% of that estimate, a deliberately wide margin precisely because "
            "the denominator is uncertain. The counter below is exact; the allowance it "
            "is measured against is not. Actual cost per call needs the Google Cloud "
            "billing console."
        ),
        configured_key="GOOGLE_PLACES_API_KEY",
        live_counter="google_places",
    ),
    Service(
        key="google_maps_js",
        name="Google Maps JavaScript",
        purpose="The interactive map on FIRE Metric.",
        used_by=("FIRE Metrics",),
        pricing_model="Per map load, billed as a separate SKU from Places.",
        plan="Pay-as-you-go with free allowance",
        monthly_cost=TBD,
        last_verified=LAST_REVIEWED,
        notes=(
            "Usage is NOT measurable from this app. The map loads in the browser and "
            "calls Google directly, so the server never observes it and no counter here "
            "could ever be accurate. The Google Cloud billing console is the only "
            "source of truth for this line."
        ),
        configured_key="GOOGLE_MAPS_API_KEY",
    ),
    Service(
        key="openai_summaries",
        name="OpenAI — AI summaries",
        purpose="Narrative market summaries on FIRE Metrics.",
        used_by=("FIRE Metrics",),
        pricing_model="Per token, in and out. Rate depends on the model.",
        plan="Pay-as-you-go",
        monthly_cost=TBD,
        last_verified=LAST_REVIEWED,
        notes=(
            "Enabled by default when OpenAI is configured. FIRE_METRICS_AI_SUMMARIES_ENABLED "
            "can be explicitly set to false to disable AI summary and CRE research spend. The model "
            "is whatever FIRE_METRICS_SUMMARY_MODEL is set to and is not fixed in code, "
            "so the per-token rate cannot be derived here. Summaries are cached per "
            "city, so re-viewing a market does not re-spend."
        ),
        configured_key="OPENAI_API_KEY",
    ),
    Service(
        key="openai_web_search",
        name="OpenAI — web search tool",
        purpose="Recent CRE market context gathered for FIRE Metrics summaries.",
        used_by=("FIRE Metrics",),
        pricing_model="Per tool call, charged ON TOP of the tokens the call consumes.",
        plan="Pay-as-you-go",
        monthly_cost=TBD,
        last_verified=LAST_REVIEWED,
        notes=(
            "The least predictable cost on this page, and listed separately from the "
            "summaries above because it bills separately. There is no local counter for "
            "it — nothing in this app knows how many searches have been run or what "
            "they cost. It shares the AI summaries' enable/disable gate and cache, "
            "which is currently the only thing bounding it. Adding a counter is a "
            "deliberate open decision, not an oversight."
        ),
        configured_key="OPENAI_API_KEY",
    ),
    Service(
        key="census",
        name="US Census / ACS",
        purpose="Population, income and home-value data for market metrics.",
        used_by=("FIRE Metrics",),
        pricing_model="Free. US government open data; the API key is free and rate-limits only.",
        plan="Free government API",
        monthly_cost="$0",
        last_verified=LAST_REVIEWED,
        notes=(
            "Called by the offline fire_metrics data-refresh scripts rather than by a "
            "user request, so it costs nothing and generates no per-visit load. Free "
            "tier is near-certain but has not been formally confirmed."
        ),
        configured_key="CENSUS_API_KEY",
        free=True,
    ),
    Service(
        key="bls",
        name="Bureau of Labor Statistics",
        purpose="Job growth data for market metrics.",
        used_by=("FIRE Metrics",),
        pricing_model="Free. US government open data; the key raises the daily request limit.",
        plan="Free government API",
        monthly_cost="$0",
        last_verified=LAST_REVIEWED,
        notes=(
            "Same shape as Census: called from the offline refresh scripts, not from a "
            "user request. Registering a key raises the daily cap but costs nothing."
        ),
        configured_key="BLS_API_KEY",
        free=True,
    ),
    Service(
        key="fema_nri",
        name="FEMA National Risk Index",
        purpose="Climate and natural-hazard risk scores.",
        used_by=("FIRE Metrics",),
        pricing_model="Free. Public ArcGIS hosted layer, no key and no account required.",
        plan="Free public dataset",
        monthly_cost="$0",
        last_verified=LAST_REVIEWED,
        notes=(
            "No API key exists for this one, so there is no configured/not-configured "
            "state to show. Read from the offline refresh scripts."
        ),
        free=True,
    ),
    Service(
        key="railway",
        name="Railway (hosting)",
        purpose="Runs the application and provides the persistent volume all tool data lives on.",
        used_by=("Everything",),
        pricing_model="Usage-based: compute plus persistent volume storage.",
        plan=TBD,
        monthly_cost=TBD,
        last_verified=LAST_REVIEWED,
        notes=(
            "Needs the actual invoice figure — nothing about Railway's billing is "
            "visible from inside the app, so this cannot be derived in code and no "
            "estimate is given here. Likely the largest single line on this page. The "
            "persistent volume is what keeps every tool's database from being wiped on "
            "each deploy, so this is not an optional cost."
        ),
    ),
)


def services_for(live_usage: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Render-ready rows: each Service as a dict, with its live-usage block
    attached when one applies.

    The route supplies `live_usage`; this module never reads a database, so
    it stays importable and testable without Flask or any SQLite file.
    """
    live_usage = live_usage or {}
    rows: list[dict[str, Any]] = []
    for svc in SERVICES:
        row: dict[str, Any] = {
            "key": svc.key,
            "name": svc.name,
            "purpose": svc.purpose,
            "used_by": svc.used_by,
            "pricing_model": svc.pricing_model,
            "plan": svc.plan,
            "monthly_cost": svc.monthly_cost,
            "last_verified": svc.last_verified,
            "notes": svc.notes,
            "free": svc.free,
            "is_tbd": svc.is_tbd,
            "configured_key": svc.configured_key,
            "usage": live_usage.get(svc.live_counter) if svc.live_counter else None,
        }
        rows.append(row)
    return rows


def tbd_count(rows: list[dict[str, Any]]) -> int:
    """How many cost figures still need a human. Shown at the top of the
    page so the gaps are the first thing read, not a footnote."""
    return sum(1 for r in rows if r["is_tbd"])
