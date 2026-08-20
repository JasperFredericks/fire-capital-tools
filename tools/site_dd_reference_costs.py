"""
FIRE Capital Tools - Site DD reference repair costs.

Researched market averages for the repairs the checklist and the item
bank can record. Pure: no Flask, no database, no network. A static table
in code, exactly like tools/service_costs.py, and for the same reasons --
these figures change slowly, editing one is a commit, and a commit keeps
its history and gets reviewed.

WHERE THESE NUMBERS CAME FROM, AND WHERE THEY DID NOT

Every figure below is an average of several independent published
contractor-estimate sources -- Angi, HomeGuide, HomeAdvisor, Fixr,
Homewyse, This Old House and similar -- researched on RESEARCHED_ON.

NOTHING HERE IS SCRAPED. There is no client, no scheduled fetch, and no
retailer is queried at runtime or at any other time. This was a one-time
manual research pass, and the module is deliberately incapable of
becoming anything else: it has no imports that could reach a network.

WHAT IS NOT PRICED, AND WHY THAT MATTERS MORE THAN WHAT IS

UNPRICED holds every item that is a real repair but could not be priced
honestly -- no consistent public figure, or a range so wide that an
average would be a fiction. Each carries the reason. That list is meant
to be read: it is what needs a real number from Michelle or a contractor,
and a guessed figure in its place would be worse than a blank, because a
capex line is believed and then budgeted against.

NOT_A_COST_ITEM is a third state, and a different claim. A water heater's
AGE is not an unpriced repair, it is not a repair at all. Collapsing the
two would put measurements on the "needs pricing" list forever.

THESE ARE NATIONAL AVERAGES, NOT QUOTES

They are a starting point for a budget, not a bid, and every one of them
is labelled as such wherever it is rendered. Local labour rates, building
scale and access can move any of these substantially -- which is why the
provenance travels with the number rather than being a footnote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The date the research pass was carried out. Every source note carries
# it, so a figure that has been sitting here for two years says so.
RESEARCHED_ON = "2026-08-15"

# Every source note must contain this phrase. A test asserts it, so a
# later edit cannot quietly present a researched average as a quote.
REQUIRED_PROVENANCE_PHRASE = "researched average"

UNIT_EACH = "each"
UNIT_SQFT = "sqft"
UNIT_LF = "lf"
UNITS = (UNIT_EACH, UNIT_SQFT, UNIT_LF)

UNIT_LABELS = {UNIT_EACH: "each", UNIT_SQFT: "per sq ft", UNIT_LF: "per linear ft"}

# A RATE IS NOT A PRICE, AND THE DIFFERENCE HAD A COST
#
# Most of this table is per-item: a range is $1,150, a smoke alarm $260.
# Seven entries are not. They are rates -- dollars per square foot of
# floor, per linear foot of run -- and they include the items that
# dominate a real budget: flooring, interior repaint, roof covering,
# facade, paving.
#
# The capex export multiplied unit cost by quantity, where quantity is
# the INSTANCE COUNT ("forty toilets"). For a per-item cost that is
# right. For a rate it multiplies dollars-per-square-foot by a count of
# things, which is not a quantity of anything. One kitchen recorded as
# needing repaint came out at $5.75 -- the rate itself, arriving with a
# count of one -- and the export declared "100% of the total is
# researched" over the top of it.
#
# So rates are named here rather than inferred at the point of use, and a
# rate with no measured quantity is not priced at all. A confidently
# wrong number is worse than an honestly missing one.
RATE_UNITS = (UNIT_SQFT, UNIT_LF)

MEASUREMENT_NEEDED = {
    UNIT_SQFT: "a measured floor area in square feet",
    UNIT_LF: "a measured length in linear feet",
}


def is_rate(unit: str | None) -> bool:
    """True when the figure is per unit of measure, not per item."""
    return unit in RATE_UNITS


# The largest researched RATE is $11.50/sqft (parking_paving) and the
# cheapest researched PER-ITEM figure is $195 (gfci, co_alarm). Seventeen
# times apart, with nothing in between.
#
# That gap is what lets a hand-typed cost on a FREEFORM item -- an item
# with no reference entry, so no unit to inherit -- be classified without
# asking anyone. A capital line priced under this figure is a rate that
# needs a quantity, not the price of a job; no capital job costs twelve
# dollars. Above it, it is a job price and totals as one.
#
# Set just above the observed rate ceiling rather than midway, so it
# refuses as little as possible.
#
# A HINT NOW, NOT A DECISION. IT NO LONGER DECIDES ANY TOTAL.
#
# This number used to classify a hand-typed cost on a freeform item:
# under it a rate, over it a job price, and the line was totalled on that
# basis. That is the guess the per-job / per-sq-ft toggle replaced.
#
# Its sole remaining caller is the unpriced REASON on such a line -- "it
# looks like a rate" / "it looks like a job price" -- which tells the
# inspector which answer is likely without letting the likelihood become
# a number. No total anywhere depends on it.
#
# It was checked against production before being demoted: there are ZERO
# findings with any stored cost at all, so nothing recorded was relying
# on the old behaviour and nothing changed value.
#
# WAS: FALLBACK ONLY, AS OF THE PER-JOB / PER-SQ-FT TOGGLE.
#
# Michelle approved the explicit choice -- "yes, please add the toggle for
# 'per sq ft' or 'per job'. It's worth the extra click to ensure the data
# is accurate." -- and an answered toggle now decides the unit outright.
# This threshold is consulted ONLY when a row carries no unit: findings
# stored before the toggle existed, and saves where nobody answered it.
#
# It is deliberately not deleted. Those rows are real and still have to be
# priced somehow, and refusing to total them all would be a worse answer
# than a heuristic that is right about the shape of the data. But it is no
# longer the primary path and should not grow new callers.
#
# PROVISIONAL. THIS IS A REASONED GUESS, NOT A DERIVED CONSTANT.
#
# The seventeenfold gap is real, but it is evidence about the 36 CURATED
# entries in this table -- and a freeform item is by definition not one of
# them. The number is being applied to exactly the category it was not
# measured on. A freeform "replace one outlet cover, $8" is a perfectly
# ordinary line and this refuses it, on the strength of an assumption
# about a field built to hold anything.
#
# The behaviour is still right: silently multiplying a rate by a headcount
# is the worse error, and a refusal is visible and correctable where a
# wrong total is neither. But nobody should later treat $15 as though it
# fell out of the data.
#
# Revisit when there is real freeform cost data to look at, and replace it
# entirely if the explicit per-item/per-unit choice on freeform costs ever
# gets built -- that removes the need to guess at all.
FREEFORM_RATE_CEILING = 15.00


def looks_like_a_rate(unit_cost: float | None) -> bool:
    """True when a figure on an item with NO known unit reads as a rate."""
    return unit_cost is not None and 0 < unit_cost < FREEFORM_RATE_CEILING


def measurement_needed(unit: str | None) -> str:
    """What has to be measured before a rate can become a total."""
    return MEASUREMENT_NEEDED.get(unit, "")


@dataclass(frozen=True)
class ReferenceCost:
    """One researched figure, with everything needed to judge it."""

    key: str
    unit_cost: float
    unit: str
    sources: tuple[str, ...]
    note: str = ""

    @property
    def provenance(self) -> str:
        return (f"Researched average of {len(self.sources)} contractor-estimate "
                f"sources ({', '.join(self.sources)}), {RESEARCHED_ON}. "
                f"National average, not a quote.")


def _c(key, cost, unit, sources, note=""):
    return ReferenceCost(key, cost, unit, tuple(sources), note)


# ── The table ────────────────────────────────────────────────────────────
#
# Each figure is the mean of the midpoints of the ranges the sources
# published, or of their own stated averages where they gave one. The
# arithmetic is recorded in the note so a reader can check it rather than
# take it on trust.

REFERENCE_COSTS: dict[str, ReferenceCost] = {

    # ── Mechanical, electrical, plumbing ─────────────────────────────
    "water_heater": _c(
        "water_heater", 1725.00, UNIT_EACH,
        ("Fixr", "Liberty Home Guard", "Sears Home Services", "HomeGuide"),
        "Like-for-like 40–50 gal tank replacement. Fixr $1,650 avg; "
        "Liberty $1,200–2,000; Sears $1,100–2,500; HomeGuide $1,200–2,500. "
        "Mean of midpoints."),
    "water_heaters": _c(
        "water_heaters", 1725.00, UNIT_EACH,
        ("Fixr", "Liberty Home Guard", "Sears Home Services", "HomeGuide"),
        "Property-scope equivalent of water_heater; same figure, per unit "
        "replaced."),
    "tankless_water_heater": _c(
        "tankless_water_heater", 2375.00, UNIT_EACH,
        ("HomeGuide", "Angi", "The Examiner"),
        "Electric $1,200–2,500, gas $2,000–3,800 installed. Mean of the two "
        "midpoints; gas venting and gas-line work is the swing factor."),
    "hvac": _c(
        "hvac", 7500.00, UNIT_EACH,
        ("Angi", "HomeAdvisor", "Pearl"),
        "Angi's stated average for replacing furnace and central AC "
        "together ($5,000–12,500). ASSUMES ONE COMPLETE RESIDENTIAL "
        "SYSTEM — a smaller per-unit system in a multifamily building will "
        "be materially less. Confirm scale before budgeting."),
    "hvac_units": _c(
        "hvac_units", 7500.00, UNIT_EACH,
        ("Angi", "HomeAdvisor", "Pearl"),
        "Property-scope equivalent of hvac; same figure and the same "
        "scale caveat, per system replaced."),
    "toilet": _c(
        "toilet", 600.00, UNIT_EACH, ("HomeGuide",),
        "HomeGuide's stated average install range of $400–800; midpoint. "
        "Single source, so treat as indicative."),
    "tub_shower": _c(
        "tub_shower", 3275.00, UNIT_EACH,
        ("This Old House", "Angi", "Homewyse", "MonBlari"),
        "Like-for-like alcove tub swap $2,000–3,000 (mid $2,500) including "
        "removal, setting and drain connection; broader bathtub "
        "replacement $1,600–6,500 (mid $4,050). Mean of the two. A "
        "TUB-TO-SHOWER CONVERSION is a different job at $4,448–12,374 and "
        "is not what this figure covers."),
    "vanity_sink": _c(
        "vanity_sink", 1170.00, UNIT_EACH, ("HomeGuide", "Fixr"),
        "Premade vanity $400–1,700 (mid $1,050) and sink with faucet "
        "$580–2,000 (mid $1,290). Mean of the two."),
    "sink_faucet": _c(
        "sink_faucet", 1290.00, UNIT_EACH, ("HomeGuide", "Fixr"),
        "Sink and faucet installed, $580–2,000; midpoint. Kitchen runs "
        "higher than bath where a disposal is involved — priced "
        "separately below."),
    "appliance_disposal": _c(
        "appliance_disposal", 375.00, UNIT_EACH,
        ("Modernize", "PlumbingJobs", "What It Actually Costs"),
        "$200–550 overall, most quoted $300–450 installed by a licensed "
        "plumber. Midpoint of the tighter range."),
    "disposal": _c(
        "disposal", 375.00, UNIT_EACH,
        ("Modernize", "PlumbingJobs", "What It Actually Costs"),
        "Item-bank equivalent of appliance_disposal; same figure."),
    "exhaust_fan": _c(
        "exhaust_fan", 325.00, UNIT_EACH,
        ("HomeAdvisor", "Homewyse", "What It Actually Costs"),
        "$350 typical for a straightforward swap; Homewyse $191–411 "
        "(mid $301). Mean of the two."),
    "gfci": _c(
        "gfci", 195.00, UNIT_EACH, ("HomeGuide", "Angi", "Networx"),
        "Replacing an existing outlet with GFCI $90–200 (mid $145); a new "
        "GFCI circuit $150–350 (mid $250). Mean. Most electricians apply a "
        "$100+ minimum, so a single outlet costs more per unit than a batch."),
    "water_softener": _c(
        "water_softener", 1500.00, UNIT_EACH, ("HomeGuide", "ProjectCostPro"),
        "$500–2,500 installed; midpoint. Grain capacity and whether a drain "
        "and loop already exist are the swing factors."),

    # ── Life safety ──────────────────────────────────────────────────
    "smoke_alarm": _c(
        "smoke_alarm", 260.00, UNIT_EACH, ("HomeGuide", "Angi", "Fixr"),
        "$110–410 per unit installed; midpoint. Per-unit cost falls "
        "sharply when several are done in one visit."),
    "smoke_alarm_unit": _c(
        "smoke_alarm_unit", 260.00, UNIT_EACH, ("HomeGuide", "Angi", "Fixr"),
        "Unit-scope equivalent of smoke_alarm; same figure."),
    "alarms_detectors": _c(
        "alarms_detectors", 260.00, UNIT_EACH, ("HomeGuide", "Angi", "Fixr"),
        "Property-scope equivalent; same figure, per detector replaced."),
    "co_alarm": _c(
        "co_alarm", 195.00, UNIT_EACH, ("HomeGuide", "Networx", "Plumbline"),
        "$120–360 (mid $240) and $75–220 (mid $148) per detector "
        "professionally installed. Mean of the two midpoints."),
    "entry_door": _c(
        "entry_door", 1450.00, UNIT_EACH,
        ("HomeGuide", "Homewyse", "Energy Home Improvements"),
        "Exterior door commonly budgeted $550–2,400 with a stated average "
        "of $1,450. A smart or electronic lock adds $160–600 on top."),

    # ── Interior and units ───────────────────────────────────────────
    # Flooring is priced per square foot BY MATERIAL, because the
    # checklist records the type separately from the condition and
    # replacing carpet is not the same line as replacing hardwood.
    "flooring": _c(
        "flooring", 6.50, UNIT_SQFT,
        ("HomeGuide", "RealCostIQ", "CostToRenovate", "ProjectCostPro"),
        "LVP/vinyl installed, $4–9/sqft, stated average $6.50 — used as the "
        "default because it is the most common multifamily specification. "
        "See FLOORING_BY_TYPE for the other materials."),
    "walls_ceiling": _c(
        "walls_ceiling", 5.75, UNIT_SQFT,
        ("HomeGuide", "Angi", "Improovy"),
        "Interior repaint including trim and ceilings, $4.70–6.75 per sqft "
        "of FLOOR area; midpoint. Walls only is roughly $3.75."),
    "walls_ceilings": _c(
        "walls_ceilings", 5.75, UNIT_SQFT,
        ("HomeGuide", "Angi", "Improovy"),
        "Property-scope equivalent of walls_ceiling; same figure."),
    "cabinets": _c(
        "cabinets", 4000.00, UNIT_EACH,
        ("HomeGuide", "HomeAdvisor", "SimplyWise"),
        "Stock cabinets $80–120 per linear foot, about $3,200–4,800 for a "
        "40 ft kitchen; midpoint. Stock rather than semi-custom because "
        "that is the multifamily specification — semi-custom runs "
        "$150–300/lf."),
    "countertops": _c(
        "countertops", 3138.00, UNIT_EACH,
        ("HomeAdvisor", "HomeGuide", "Family Interiors"),
        "Stated US average for a kitchen countertop replacement; most "
        "spend $1,850–4,450. Reflects owner-occupier material choices and "
        "is likely high for a rental specification."),
    "appliance_range": _c(
        "appliance_range", 1150.00, UNIT_EACH, ("HomeGuide", "HomeAdvisor"),
        "Unit $600–1,300 (mid $950) plus $100–300 replacement labour "
        "(mid $200)."),
    "appliance_fridge": _c(
        "appliance_fridge", 1640.00, UNIT_EACH, ("HomeGuide", "HomeAdvisor"),
        "16–25 cu ft unit $600–2,300 (mid $1,450) plus $130–250 install "
        "(mid $190)."),
    "appliance_microwave": _c(
        "appliance_microwave", 350.00, UNIT_EACH, ("HomeGuide", "HomeAdvisor"),
        "$100–600 for the unit; midpoint. An over-range install with "
        "venting costs more than a countertop swap."),
    "washer": _c(
        "washer", 925.00, UNIT_EACH, ("HomeGuide", "HomeAdvisor"),
        "Washer and dryer set $1,000–2,300 (mid $1,650) plus $100–300 "
        "install with existing hookups; halved for one machine."),
    "dryer": _c(
        "dryer", 925.00, UNIT_EACH, ("HomeGuide", "HomeAdvisor"),
        "Same derivation as washer; half of a set plus install."),
    "washer_dryer": _c(
        "washer_dryer", 1850.00, UNIT_EACH, ("HomeGuide", "HomeAdvisor"),
        "The pair: set $1,000–2,300 (mid $1,650) plus $100–300 install "
        "with existing hookups (mid $200)."),

    # ── Structural and envelope ──────────────────────────────────────
    "windows": _c(
        "windows", 1125.00, UNIT_EACH,
        ("Homewyse", "HomeFix", "NerdWallet"),
        "Homewyse $632–967 (mid $800); broader market $400–2,500 "
        "(mid $1,450). Mean of the two midpoints."),
    "windows_doors": _c(
        "windows_doors", 1125.00, UNIT_EACH,
        ("Homewyse", "HomeFix", "NerdWallet"),
        "Property-scope equivalent of windows; same figure, per opening."),
    "roof_covering": _c(
        "roof_covering", 6.31, UNIT_SQFT,
        ("Angi", "This Old House", "Modernize"),
        "$4–11/sqft across materials (mid $7.50); asphalt shingle quoted "
        "at $5,117 per 1,000 sqft ($5.12/sqft). Mean of the two."),
    "roof_drainage": _c(
        "roof_drainage", 8.17, UNIT_LF,
        ("Angi", "RoofRiverCity", "HomeGuide"),
        "Gutters $5–14/lf (mid $9.50); 5-inch aluminium about $6/lf, "
        "6-inch about $9/lf. Mean of the three."),
    "facade_siding": _c(
        "facade_siding", 7.43, UNIT_SQFT,
        ("Angi", "Fixr", "This Old House"),
        "$4–13/sqft installed across materials (mid $8.50); vinyl "
        "$4.50–8.20 (mid $6.35). Mean of the two."),

    # ── Site and exterior ────────────────────────────────────────────
    "parking_paving": _c(
        "parking_paving", 11.50, UNIT_SQFT,
        ("Angi", "TruTec", "Remodel Cost Calculator"),
        "Asphalt replacement $8–15/sqft including removal of the existing "
        "surface; midpoint. Resurfacing alone is far less."),
}


# Flooring by material, since the checklist records the type separately.
# Same sources and the same date as the flooring entry above.
FLOORING_BY_TYPE: dict[str, float] = {
    "carpet": 3.50,
    "laminate": 5.00,
    "vinyl": 6.50,          # LVP / LVT
    "hardwood": 12.75,      # engineered $10.50, solid $15.00
    "tile": 13.50,          # porcelain/ceramic $7–20
    "concrete": 0.0,        # sealed/polished varies far too widely — see UNPRICED
}

FLOORING_SOURCES = ("HomeGuide", "RealCostIQ", "CostToRenovate", "ProjectCostPro")


# ── Deliberately unpriced ────────────────────────────────────────────────
#
# Real repairs that could not be given an honest figure. THIS LIST IS THE
# DELIVERABLE, not the leftovers: it is what needs a number from Michelle
# or from a contractor, and every entry says why rather than just being
# absent.

UNPRICED: dict[str, str] = {
    # ── Added with the v7 checklist items ────────────────────────────────
    #
    # No figure is invented for any of these. Each is a real repair with a
    # real cost; none has a published national average that would survive
    # being quoted back to a contractor.
    "mold": (
        "Remediation is priced from the affected area and the source of "
        "the moisture, and a suspected finding needs testing before any "
        "scope exists. Published figures span $500 to $30,000 for the "
        "same words, so none of them is usable as a default."),
    "thermostat": (
        "A swap for a like-for-like unit and a smart-thermostat upgrade "
        "differ by an order of magnitude, and which one applies is a "
        "decision about the property rather than an observation about "
        "the thermostat. Enter the figure for the unit actually chosen."),
    "fire_extinguisher": (
        "An out-of-date tag needs a service visit; a missing extinguisher "
        "needs a unit and a bracket. The two are priced differently and "
        "the checklist deliberately records which applies, so pricing "
        "them with one figure would discard that."),
    # Electrical — GFCI is priced, plain devices are not
    "outlets_switches": (
        "No consistent published figure for replacing a standard outlet or "
        "switch. GFCI replacement is priced ($195); a plain device is less "
        "but by an amount the sources do not state, and most electricians "
        "charge a call-out minimum that dominates a single-device job."),
    "lighting": (
        "'Lighting' spans a $40 lamp replacement and a $900 fixture "
        "install. No usable single figure."),

    # Plumbing / mechanical with no consistent public figure
    "dryer_vent": (
        "Cleaning ($100–200) and re-ducting ($200–700+) are different jobs "
        "and the checklist item does not distinguish them."),
    "sump_pump": "No consistent published installed figure found.",
    "visible_leaks": (
        "A symptom, not a defined repair. The cost is whatever the leak "
        "turns out to be."),
    "wd_hookups": (
        "Adding drain, vent and 240V where none exist varies with the "
        "distance to existing services. No usable average."),
    "ceiling_fan": "No consistent published installed figure found.",
    "window_ac": "No consistent published installed figure found.",
    "baseboard_heater": "No consistent published installed figure found.",
    "appliance_dishwasher": (
        "Install labour is published ($110–270) but the appliance itself is "
        "not quoted separately in the sources found, so a total would be "
        "half-researched and half-guessed."),

    # Building-scale systems with no per-unit figure
    "electrical_panels": (
        "Panel upgrades and rewiring are quoted per building and per "
        "amperage. No per-unit figure applies."),
    "plumbing_supply": "Quoted per run and per material; no per-unit figure.",
    "waste_sewer": "Quoted per linear foot of the actual defect; scope-dependent.",
    "ventilation": "Spans a bathroom fan to a building-wide system.",

    # Life safety with no consistent figure
    "egress_window": (
        "Cutting a new egress opening and replacing an existing one differ "
        "by an order of magnitude."),
    "security_screen_door": "No consistent published installed figure found.",
    "extinguishers_sprinklers": (
        "Extinguisher service is trivial; sprinkler work is a fire-"
        "protection contract. Not one number."),
    "egress_signage": "No consistent published figure found.",
    "stairs_railings": "Entirely dependent on material, run and code scope.",
    "security_lighting": "No consistent published figure found.",

    # Envelope / structure — genuinely scope-dependent
    "foundation": (
        "Ranges from a crack seal to underpinning. Averaging these would "
        "produce a number that is wrong for every real case."),
    "framing_walls": "Structural repair is scoped by an engineer, not averaged.",
    "skylight": "No consistent published installed figure found.",

    # Site
    "drainage_grading": "Scope-dependent; no usable average.",
    "landscaping": "Spans mowing to full replanting.",
    "exterior_lighting": "No consistent published figure found.",
    "signage_fencing": "Fencing is per linear foot by material; signage is bespoke.",
    "balcony_patio": "Structural repair; scope-dependent.",
    "garage_carport": "No consistent published figure for repair as opposed to new build.",

    # Interior extras with no published figure
    "closet": (
        "No published figure. Repairing a closet spans rehanging a door, "
        "replacing a rod and shelf, and rebuilding the whole thing, and no "
        "source separates them."),
    "walk_in_closet": (
        "As closet, and more so — a walk-in is joinery priced per job, with "
        "no published per-unit rate."),
    "pantry": (
        "As closet. Shelving, door and finish are quoted together per job "
        "and no source publishes a rate."),
    "linen_closet": (
        "As closet. Too small and too variable for any source to publish a "
        "figure worth averaging."),
    "storage_locker": "No consistent published figure found.",
    "wet_bar": "Bespoke; no usable average.",
    "half_bath": (
        "Adding a half-bath is a project quote, not a unit cost, and the "
        "item records the presence of one rather than work on it."),
    "fireplace": (
        "Inspection, flue relining and full rebuild differ by an order of "
        "magnitude."),
    "concrete_flooring": (
        "Sealed and polished concrete range too widely to average."),

    # Property-scope rollups: these summarise a category rather than
    # naming a repair, so a unit cost would be meaningless.
    "kitchens": "A category roll-up, not a single repair. Price its parts.",
    "bathrooms": "A category roll-up, not a single repair. Price its parts.",
    "unit_appliances": "A category roll-up. The individual appliances are priced.",

    # Accessibility and environmental — specialist assessment first
    "ada_parking_path": "Scoped by an accessibility survey, not averaged.",
    "ada_common_areas": "Scoped by an accessibility survey, not averaged.",
    "moisture_mould": "Remediation is scoped after testing.",
    "pest_evidence": "Treatment is scoped after inspection.",
    "hazmat_indicators": (
        "Asbestos and lead work is quoted after testing and is regulated. "
        "Never estimate this."),
}


# ── Not a cost item at all ───────────────────────────────────────────────
#
# A different claim from "unpriced". A water heater's AGE is not a repair
# waiting for a price; it is a measurement. Keeping these separate stops
# them sitting on the "needs pricing" list forever.

NOT_A_COST_ITEM: dict[str, str] = {
    # Which species, not what to do about it. The work and its cost hang
    # off pest_evidence; this records what was found so the treatment can
    # be specified. Pricing it would double-count the same job.
    "pest_type": (
        "Identifies the pest rather than the work. The remediation cost "
        "belongs to pest_evidence."),
    "flooring_type": "Records what the floor is, not work on it.",
    "water_heater_gal": "A capacity measurement.",
    "water_heater_age": "An age measurement.",
    "hvac_age": "An age measurement.",
}


# ── Lookup ───────────────────────────────────────────────────────────────

def for_item(item_key: str, flooring_type: str | None = None
             ) -> ReferenceCost | None:
    """The researched cost for one item, or None.

    None covers all three of "unpriced", "not a cost item" and "unknown
    key" on purpose: a caller deciding whether to put a number on a line
    only needs to know there isn't one. Use `status()` when the reason
    matters.
    """
    if item_key in ("flooring",) and flooring_type:
        rate = FLOORING_BY_TYPE.get(flooring_type)
        if rate:
            return _c("flooring", rate, UNIT_SQFT, FLOORING_SOURCES,
                      f"{flooring_type.title()} installed, researched average "
                      f"per square foot.")
        return None if flooring_type in FLOORING_BY_TYPE else REFERENCE_COSTS.get("flooring")
    return REFERENCE_COSTS.get(item_key)


def status(item_key: str) -> str:
    """'priced' | 'unpriced' | 'not_a_cost_item' | 'unknown'."""
    if item_key in REFERENCE_COSTS:
        return "priced"
    if item_key in NOT_A_COST_ITEM:
        return "not_a_cost_item"
    if item_key in UNPRICED:
        return "unpriced"
    return "unknown"


def reason(item_key: str) -> str:
    return UNPRICED.get(item_key) or NOT_A_COST_ITEM.get(item_key) or ""


def unpriced_report(labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The list that goes to Michelle, sorted for reading."""
    labels = labels or {}
    return sorted(
        ({"key": k, "label": labels.get(k, k), "reason": v}
         for k, v in UNPRICED.items()),
        key=lambda r: r["label"].lower())


