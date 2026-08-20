# Work recorded on a choice item never reaches the budget

**Investigation and proposal. Written 2026-08-19 against master at
`805bb9d`.**

> ## FIXED — shipped 2026-08-19 in `8b8ba17`
>
> **Sections 1 to 4 describe a gap that is closed. Do not re-investigate
> it.** The rest of this document is kept because the reasoning is still
> the reason the code looks the way it does, and because sections 2 and 5
> contain findings that are still open.
>
> | section | status |
> |---|---|
> | 1 — extent, both populations | fixed. Verified on production: a missing smoke alarm, a GFCI that does not trip and an absent HVAC each produce a priced line. |
> | 2 — the two false honesty messages | fixed, and confirmed on deployed code rather than assumed repaired by the upstream change. |
> | 2 — `to_capex_lines()` has no live callers | **STILL OPEN.** The fifth dead path. Queued. |
> | 3 — GFCI present-but-not-tripping | was already handled; the stale note in the checklist that claimed otherwise is now corrected in the source. |
> | 3 — presence details cannot reach the budget | fixed. This was the gap. |
> | 4 — the rule | shipped as proposed: `uc.WORK_OPTIONS` keyed by option set, one `uc.needs_work()`. |
> | 5 — neither existing sweep can catch this | unchanged, and now has a third sweep beside them: `tests/test_budget_reachability.py`. |
> | 5 — widening the dead-reader glob | **STILL OPEN.** To be weighed on its own false-positive rate. Queued. |
>
> **Three things shipped that this document did not propose**, each a
> consequence of admitting these findings rather than an extension of
> scope:
>
> 1. **`detail` joined the grouping key.** Without it, a missing alarm and
>    one needing replacement — same item, same room, same $260 — would
>    have collapsed into "Smoke alarm ×2". Admitting the findings without
>    this would have swapped a silent drop for a silent merge.
> 2. **Lines carry a `state` field.** A choice finding has no condition,
>    so both exports would have printed "—" beside a $260 request. It
>    echoes the option label the inspector saw: "Present, not working",
>    never `not_working`.
> 3. **The capture screen's cost box opens on the same predicate.** Section
>    2 noted the box stayed collapsed on a missing alarm, hiding even the
>    manual override. The screen and the export now share one call, so
>    they cannot disagree. `work_conditions` was consequently dead
>    template context and was removed.

`site_dd.py:1129` is the whole defect:

```python
work = [f for f in findings if f.get("condition") in cond.WORK_CONDITIONS]
```

A finding is admitted to the capital budget if and only if its
**condition** is `repair` or `replace`. Choice items record their answer
in `detail`, not `condition`. So a smoke alarm recorded as **missing**, a
GFCI recorded as **present, not working**, and a range recorded as **not
there** are all discarded before pricing — each of them a state an
inspector deliberately selected, and three of them with a researched
figure sitting in `REFERENCE_COSTS` that nothing can ever apply.

---

## 1. Extent

Measured with an instrument, not by reading: every catalogue item, every
state an inspector can record on it, pushed through the real pipeline
(`apply_reference` → filter → `build_lines`) exactly as
`site_dd.capex_export()` assembles it.

Positive and negative controls first, per the standing rule — an
instrument that has never returned a difference has not been tested:

```
positive control: toilet/replace -> 1 line at $600.00   OK
negative control: toilet/good    -> no line             OK
```

**39 catalogue items: 25 can reach the budget, 14 can never.**

### 1a. Structurally unreachable — no input can ever price them

Four items are `with_condition=False`. They have no condition field at
all, so no input to the form can put them past the filter. All four are
priced.

| item | figure | where | states it can hold |
|---|---|---|---|
| `smoke_alarm` | $260.00 | every bedroom | working / **missing** / **replace** |
| `smoke_alarm_unit` | $260.00 | unit-wide | working / **missing** / **replace** |
| `co_alarm` | $195.00 | unit-wide | working / **missing** / **replace** |
| `gfci` | $195.00 | kitchen + every bathroom | present / **not working** / **absent** |

Three of the four are **life safety**. `smoke_alarm` and `gfci` are
per-room, so a three-bedroom two-bathroom unit carries five of them.

Note `smoke_alarm` can hold the literal value **`replace`** — a string
that is *in* `WORK_CONDITIONS` — and is still discarded, because it is
stored in `detail` and the filter reads `condition`. The value is right,
the column is wrong, and nothing reports it.

Ten further items are unreachable and **unpriced**, so nothing is lost
today, but each becomes a silent loss the moment it is priced:
`egress_window`, `fire_extinguisher`, `mold`, `pest_evidence`,
`visible_leaks` (unpriced) and `flooring_type`, `pest_type`, `hvac_age`,
`water_heater_age`, `water_heater_gal` (`not_a_cost_item`, correctly
excluded and listed only for completeness).

### 1b. Reachable in principle, unreachable in practice — the larger half

Ten more items are `KIND_CHOICE` **with** `with_condition=True`, so the
instrument classes them reachable: an inspector *could* tick both
`absent` and `replace`. But the form tells them not to. `site_dd_room.html`
renders the condition under the heading:

> **Condition, if present**

and the comment above it states the intent plainly: *"an appliance that
was never installed has no condition, and asking for one would invite a
rating of something that is not there."* That is correct form design. It
also means the state an inspector actually stores for a missing appliance
is `(condition=NULL, detail='absent')` — and that state produces nothing.

| item | figure | blank-condition states that vanish |
|---|---|---|
| `hvac` | $7,500.00 | missing, replace |
| `water_heater` | $1,725.00 | missing, replace |
| `appliance_fridge` | $1,640.00 | hookup_only, absent |
| `appliance_range` | $1,150.00 | hookup_only, absent |
| `washer` | $925.00 | hookup_only, absent |
| `dryer` | $925.00 | hookup_only, absent |
| `appliance_disposal` | $375.00 | hookup_only, absent |
| `appliance_microwave` | $350.00 | hookup_only, absent |
| `exhaust_fan` | $325.00 | hookup_only, absent |
| `appliance_dishwasher` | unpriced | hookup_only, absent |

**One stripped unit, every priced item recorded honestly by the
inspector, comes out at $0.00 against $15,825.00 of researched figures.**

The two populations differ in kind and the fix must cover both. 1a is a
missing capability; 1b is a filter that disagrees with the form beside it.

### 1c. What Michelle would actually see

A unit with the alarms gone, no range, and a dead GFCI — four findings, a
plausible walk:

```
findings recorded by the inspector : 4
survive the WORK_CONDITIONS filter : 0
budget lines produced              : 0

what the PDF and the XLSX both print:
    No items were recorded as needing work.

researched figures that existed the whole time:
    smoke_alarm_unit     $  260.00
    co_alarm             $  195.00
    gfci                 $  195.00
    appliance_range      $1,150.00
                         $1,800.00  reported as $0 / "no items"
```

---

## 2. Downstream

**`coverage_sentence()` states two things that are false under this gap,
and the Part 28 audit passed both.** The audit was not wrong: it checked
each sentence against the lines the code produces, and against those
lines both sentences are exactly true. The defect is upstream of the
lines, where findings are discarded before they can become any.

1. **`"No items were recorded as needing work."`** — printed whenever
   `total_lines == 0`. In the case above, four items *were* recorded as
   needing work. This is the same class of failure as the RentCast
   disclosure: a sentence asserting something the code has not
   established. It is worse than a missing number, because it tells the
   reader there is nothing to look for.
2. **`"All N lines priced. This total is the whole recorded budget."`** —
   "whole **recorded** budget" was passed as carefully qualified. Under
   this gap the qualifier is the part that is wrong: things were
   recorded and are not in it.

Both sentences are written once and consumed by **the PDF and the XLSX
alike** — deliberately, so the two cannot describe the same budget
differently. That design works exactly as intended here: both are wrong
in the same way.

**The unit roll-up also reads zero, and there it is correct.** Verified:
`summarize_unit` on the same unit returns `work_count = 0`. That is
documented and deliberate — *"Only conditions are counted. A choice like
'Hookup only' is a fact about the unit, not a rating, and totalling it
alongside wear states would produce a number that means nothing."* Fair
for a completion percentage. The consequence is that **nothing anywhere
flags the unit**: the roll-up says no work, the budget says no lines, the
coverage sentence says nothing was recorded. Three independent surfaces,
one blind spot, no disagreement to notice.

**The capture screen hides the manual escape hatch too.** Both templates
open the cost box with:

```jinja
{{ 'open' if est.has_cost or (row and row.condition in work_conditions) else '' }}
```

So on a missing smoke alarm the estimate box renders **collapsed**. The
researched $260 is inside it — `reference_hint()` is not gated on
condition — but nothing invites the inspector to look. Even the override
path is closed by the same assumption.

**Underwriting is unaffected, because nothing reaches it at all.**
`to_capex_lines()` is the hand-off that would write `SOURCE_SITE_DD` rows,
and it has **zero live callers**. Its only three mentions outside its own
module and tests are prose in comments — in `site_dd_capex_export.py`
(twice) and `site_dd_db.py`. That is precisely the failure mode the
dead-reader sweep was rewritten to catch, and it is invisible to that
sweep because `site_dd_costs.py` is not a `*_db.py` file. **This is the
fifth dead path, found while investigating the fourth.**

---

## 3. Deliberate, or an oversight?

**An oversight, and the evidence is in the commit that introduced it.**

Order of events, from the history:

| commit | date | what |
|---|---|---|
| `0033990` | 2026-08-13 | branch 1: `WORK_CONDITIONS` defined, property scope only |
| `47f232b` | 2026-08-13 | branch 2: `KIND_CHOICE`, `with_condition`, the `detail` column |
| `0dbd3df` | 2026-08-14 | the capex export, and the filter |

So the filter was written **a day after** choice items existed, not
before. It is not a case of the vocabulary predating the concept.

The comment it shipped with is the tell:

```python
# Only findings that actually record a problem reach the budget. A
# water heater in good order is not a capital line.
```

The stated intent is *"findings that actually record a problem"*. The
code implements *"findings whose condition is repair or replace"*. Those
are different sets, and the gap between them is this entire document. The
example the comment reaches for — `water_heater` — is **itself a choice
item**, one of the ten in section 1b.

`WORK_CONDITIONS`' own definition says it exists *"so the definition of
'needs work' cannot drift between the summary, the export and the capex
hand-off."* It succeeded at that. It could not prevent drift between the
definition and a second kind of item that did not exist when it was
written.

Nothing in the code argues for the exclusion. There is no comment
anywhere saying a missing alarm should not be a budget line, and the
reference table prices three of them — which is an argument in the
opposite direction, made by the same author two days later.

---

## 4. The rule, which is the part that matters

The question is what "this option value means work is needed" is, in
general, for an item with no condition. Getting this wrong per-item is
how the next silent gap gets built.

### A global set of work-implying values is provably wrong

Checked, not assumed. Two values mean **opposite things** on different
items:

| value | means work on | means no work on |
|---|---|---|
| `present` | `mold` (mold is present) | the 8 `PRESENCE` appliances (the appliance is there) |
| `none` | `egress_window` ("No egress window") | `mold`, `pest_evidence`, `visible_leaks` ("None seen") |

So `WORK_OPTIONS = {"missing", "absent", "present", …}` cannot exist. Any
rule that keys on the value alone is wrong on at least these four items.

### Per-item lists are the scattered logic to avoid

21 items carry options. A per-item map is 21 entries that must each be
remembered when an item is added — which is the mechanism that produced
this gap in the first place.

### The rule: work-ness is a property of the option set, declared beside it

This is exactly what `WORK_CONDITIONS` already is — a property of the
condition *scale*, declared once, next to the scale, in the module that
owns it. The equivalent for choices is a property of the *option set*,
declared once, next to the option set, in the module that owns those.

There are **only ten distinct option sets** across the whole unit
checklist, and two of them cover thirteen of the twenty-one items:

| option set | items | work-implying values |
|---|---|---|
| `PRESENCE` | 8 appliances | `hookup_only`, `absent` |
| `ALARM_STATES` / `EQUIPMENT_STATES` | 5 | `missing`, `replace` |
| gfci (inline) | 1 | `not_working`, `absent` |
| `EXTINGUISHER_STATES` | 1 | `expired`, `missing` |
| egress_window (inline) | 1 | `restricted`, `none` |
| visible_leaks (inline) | 1 | `minor`, `active` |
| `MOLD_STATES` | 1 | `suspected`, `present` |
| `PEST_EVIDENCE` | 1 | `droppings`, `live`, `damage` |
| `FLOORING_TYPES` | 1 | none — `not_a_cost_item` |
| `PEST_TYPE` | 1 | none — `not_a_cost_item` |

Concretely: a module-level registry keyed by the option-set tuple, so
each set declares its own answer once and every item using it inherits.

```python
WORK_OPTIONS: dict[tuple, frozenset[str]] = {
    PRESENCE:        frozenset({"hookup_only", "absent"}),
    ALARM_STATES:    frozenset({"missing", "replace"}),
    GFCI_STATES:     frozenset({"not_working", "absent"}),
    ...
}

def needs_work(item, condition, detail) -> bool:
    if cond.needs_work(condition):
        return True
    return detail in WORK_OPTIONS.get(item.get("options") or (), ())
```

and the filter becomes `work = [f for f in findings if needs_work(...)]`,
one call, one definition, mirroring `cond.needs_work(value)`.

Four things this design has to own, stated rather than discovered later:

1. **Three option sets are currently inline literals** (`gfci`,
   `egress_window`, `visible_leaks`) and must be promoted to named
   constants to be registry keys. That is an improvement regardless:
   `gfci`'s tuple is written out **twice**, once in `KITCHEN` and once in
   `BATHROOM`, which is a live drift risk today.
2. **`ALARM_STATES` and `EQUIPMENT_STATES` are equal tuples**, so a
   dict keyed by the tuple silently merges them into one row. Today they
   should have the same answer, so this is harmless — but it means the
   two can never be given different rules while their values match.
   Either accept that and say so, or collapse them into one constant.
3. **The item bank needs covering too.** Three bank items are
   `default_kind='choice'`: `washer_dryer` (**priced $1,850**),
   `disposal` (**$375**), and `wd_hookups` (an eleventh option set,
   `complete / partial / absent`, unpriced). They are not in the unit
   checklist and would be missed by a checklist-only registry.
4. **A completeness test, not discipline.** Every option set reachable
   from the catalogue or the bank must have a registry row — absent is a
   failure, not a default of "no work". That is what stops the next item
   from re-opening this gap.

### Smallest correct fix

1. The registry and `needs_work(item, condition, detail)`. No behaviour
   change yet.
2. The completeness test above, plus a reachability test in the shape of
   section 1's instrument: **every priced item must have at least one
   recordable state that produces a budget line.**
3. Change the one filter at `site_dd.py:1129`.
4. Open the capture screen's cost box on the same predicate, so the two
   templates and the export agree by construction rather than by having
   the same author.

Step 3 is the only one that changes a number, and it changes it from
wrong to right.

**Two things explicitly out of scope.** No costs are invented — the ten
unpriced items in 1a stay unpriced and will simply arrive as lines with
"no researched figure", which is the honest outcome the three-bucket
summary already exists to report. And nothing an inspector records
changes: the form keeps saying "Condition, if present", because that
instruction is correct.

---

## 5. Could either sweep catch this shape?

**No, and it is genuinely a different shape — but it is mechanically
checkable, and cheaply.**

The four prior instances — `list_feedback`, `list_updates`, the notetaker
itself, the unlinked capex exports — plus the fifth found here,
`to_capex_lines`, are all the same thing: **a function nothing calls.**
Both sweeps are call-graph and reference sweeps and that is the right
instrument for that shape.

This is not that. `build_lines` is called. `apply_reference` is called.
`REFERENCE_COSTS["co_alarm"]` is read on every export. **Every function
involved has a live caller, and the sweep is right to pass.** What is
dead is a *value*: $195 that no input can reach, because a predicate
upstream excludes every finding that could carry it. That is
unreachability in the data domain, and no reference-counting sweep can
see it.

So neither sweep extends to it. But the third sweep it implies is small
and this document already contains a working version of it:

> **For every priced item in the reference table, there must exist at
> least one state an inspector can record that produces a budget line.**

That is a bounded enumeration — 39 items, at most ~40 states each — it
runs against the real pipeline in well under a second, it takes controls
the way the other two sweeps do, and it would have failed on the day
`0dbd3df` landed. It generalises past this bug: it also catches a future
item priced but never added to a checklist, an item whose category maps
to nothing, and a rate item with no route to a measurement.

### The control that could not fail, and how it was caught

*Added after building it.* The sweep shipped as
`tests/test_budget_reachability.py`, and its positive control took two
attempts. That is worth recording, because the first attempt is the
failure mode the standing rule exists to prevent.

The control was written as: **empty `ALARM_STATES`' entry in the registry
and require the sweep to report `smoke_alarm` by name.** It did not. The
sweep passed, reporting nothing dead, and a passing positive control is
indistinguishable from a broken instrument.

The reason was not a bug. `needs_work()` carries a third rule — a
work-condition string found in `detail` is work whatever the registry
says — which exists precisely because `ALARM_STATES` stores the literal
value `replace`. So emptying the registry row did not make the alarm
unreachable: rule 3 still admitted `replace`. **The instrument was
correct and the probe was wrong.**

The control now empties `GFCI_STATES` and requires the sweep to name
`gfci`, which nothing can rescue: `with_condition=False`, so there is no
condition to fall back on, and none of its values is a work-condition
string. That is exactly the shape of the original bug.

**The general lesson, which belongs beside "every comparator gets a
positive control before it is trusted": a positive control has to probe a
path with no redundant rescue.** A defence-in-depth design will quietly
absorb a single removed guard, and the control will then certify an
instrument nobody has actually tested. This one was caught only by
running it and being surprised. The surviving behaviour was worth keeping
either way and is now pinned by its own test, so rule 3 cannot be deleted
later as redundant.

**One extension to the existing dead-reader sweep is worth taking
separately**, and is unrelated to the rule above: its scope is
`tools/*_db.py`, which is why `to_capex_lines` — a public mapper in
`site_dd_costs.py` with no caller — is invisible to it. Widening the file
glob would have found the fifth instance. That is a change to the
existing sweep's reach, not a new instrument, and it should be weighed on
its own false-positive rate rather than folded into this work.
