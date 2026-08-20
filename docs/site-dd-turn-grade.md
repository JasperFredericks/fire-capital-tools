# Turn grade — design

**Design only. No schema change, no code. Written 2026-08-20 against
master at `5cde052`.**

Paresh's v7 form asks one question per unit that ours does not:

```
unit_grade:  l1 -> "L1 - Light Repair"
             l2 -> "L2 - Medium"
             l3 -> "L3 - Heavy Turn/Remodel"
```

It is a **summary judgment made by a person standing in the unit**. We
compute a roll-up from findings. The two can disagree, and section 5
argues about whether the grade earns its place at all.

---

## 1. The pattern this follows

`underwriting_property.resolve()` is the house answer for a human figure
disagreeing with a computed one, and its module docstring states the rule
directly:

> An override does not replace the derived figure quietly — it replaces
> it *visibly*.
>
> | | |
> |---|---|
> | no override | the derived figure is used, and it is the only figure on screen |
> | override, agrees | the override is used; nothing to say |
> | override, disagrees | the override is used, AND both figures are reported along with the gap |
>
> A silent overwrite is the failure this shape exists to prevent.

`_field()` returns `{value, derived, override, source, disagrees, gap,
note, label}` with four `source` states: `none` / `derived` /
`override_agrees` / `override_disagrees`. The turn grade is the same
shape with two substitutions — the "override" is the inspector's grade,
and the comparison is ordinal rather than numeric.

**One thing does not carry over.** For unit count, the entered figure
*wins*: `used = override if override is not None else derived`. That is
right there, because a person who counted the units has better
information than a rent roll. It is **not** right here. See section 4.

---

## 2. Where the grade lives, and what its values mean

**Per unit, on `site_dd_areas`** — a nullable `turn_grade TEXT`. A grade
is a judgment about one dwelling, which is how Paresh asks it. Not on the
assessment: a building's grade is a different claim and would need its
own derivation.

Nullable matters. Most units will never carry one, and "not graded" must
read as absent rather than as L1.

```python
TURN_L1 = "l1"; TURN_L2 = "l2"; TURN_L3 = "l3"
TURN_GRADES = (TURN_L1, TURN_L2, TURN_L3)

TURN_GRADE_LABELS = {
    TURN_L1: "L1 — Light repair",
    TURN_L2: "L2 — Medium",
    TURN_L3: "L3 — Heavy turn / remodel",
}
```

**With the label map from the start**, per `AREA_STATUS_LABELS` — these
values are multi-word by nature and `{{ grade|title }}` would render
`L3 - Heavy Turn/Remodel` as a stored key. That trap is now documented;
it should not be re-entered.

Paresh's wording is kept because it is a mature instrument's vocabulary
and because "L2" is what an asset manager says out loud. What each band
*means in dollars* is section 3's problem, and it is Michelle's number.

---

## 3. What the grade is compared against

This is the load-bearing choice: the comparison has to be one an
inspector would accept as fair, or the disagreement notice becomes noise
they learn to ignore.

### Rejected: completion percentage

`summarize_unit()` returns `completion_pct`. It measures **how much of
the inspection is done**, not how bad the unit is. A unit 40% walked is
not thereby L1. Wrong axis entirely; listed only because it is the most
available number and someone will reach for it.

### Rejected: worst condition

`worst` / `worst_label` — a single `replace` anywhere makes the unit L3.
One dead toilet in an otherwise sound unit is not a heavy remodel, and an
inspector told so would stop reading the notice. Too brittle to be fair.

### Rejected on its own: count of `WORK_CONDITIONS` items

`work_count` is closer and still wrong twice over. It is
**scale-dependent** — five work items in a studio and five in a four-bed
are different situations — and it weighs a $35 toilet seat and a $7,500
HVAC identically. It also cannot see choice-item work at all:
`summarize_unit` deliberately counts only conditions, so a unit with no
alarms and no range has `work_count = 0`.

### The comparator: the unit's own capex subtotal, banded

**L1/L2/L3 are cost bands wearing adjectives.** "Light repair" versus
"heavy turn" is a statement about money, and dollars is the only
comparator an inspector will recognise as fair, because it is the thing
their grade was a shorthand for.

This became possible only recently. Before `8b8ba17` a stripped unit
priced at $0.00, so a dollar comparator would have called every gutted
unit L1 — the derived side has to be trustworthy before it can contradict
anybody.

```
derived_grade = band(sum of priced lines for this area)
```

reusing `build_lines()` filtered to the area, so the grade and the budget
cannot drift.

**The bands are Michelle's numbers, not mine.** Indicative only, and they
should carry the same PROVISIONAL labelling `FREEFORM_RATE_CEILING` does
until she sets them:

| band | indicative | what it is |
|---|---|---|
| L1 | under ~$2,500 | paint, patch, clean, a fixture or two |
| L2 | ~$2,500–8,000 | flooring plus appliances, or a bathroom |
| L3 | over ~$8,000 | cabinets, HVAC, or a gut |

### The part that makes this honest: refuse to derive when coverage is thin

The subtotal is **partial** by design — unpriced items, and rates with no
measurement, which under the Step E constraint will never be measured.
So a derived grade must not be stated as though the budget were complete.

`summarize()` already reports exactly what is needed: `priced`,
`unmeasured` and `unresearched` line counts. So:

- **No priced lines** → no derived grade. `source: "none"`, and the note
  says so rather than defaulting to L1.
- **Some lines unpriced** → the subtotal is a **floor**, so the derived
  grade is a floor too: *"at least L2"*. An incomplete budget still
  supports a lower bound, and a lower bound is enough to contradict an
  inspector who said L1. This is the strongest property of the dollar
  comparator and no other candidate has it.
- **Everything priced** → a plain derived grade.

That maps onto `_field()` cleanly: `derived` is the band, and a new
`derived_is_floor` flag distinguishes "L2" from "at least L2".

---

## 4. Which figure wins, and where the disagreement shows

### Neither wins — and that is a deliberate departure from `resolve()`

For unit count, the entered figure wins. Here **the grade must not
override the budget**, because the budget is what the capex export and
(eventually) Underwriting consume. A grade that could overwrite a
line-item total would let "L1" quietly erase $14,000 of researched
findings.

So the grade is **recorded and displayed, never substituted**. The
resolved object keeps `resolve()`'s shape and its `value` is used for
display only:

```python
{"value": "l2", "derived": "l3", "override": "l2",
 "source": "override_disagrees", "derived_is_floor": True,
 "gap": -1,            # bands apart, signed
 "note": "..."}
```

`gap` as *bands apart* rather than a dollar difference, because the two
sides are not the same kind of quantity — that is the honest arithmetic,
and it makes `tolerance = 0` (adjacent bands disagree) the natural
default.

### The sentence, written once

The three-bucket coverage sentence is the house precedent, and its
docstring says why it lives in one place:

> Written once, here, because the PDF and the XLSX must not be able to
> describe the same budget differently.

The turn-grade sentence gets the same treatment — one function, consumed
by every surface:

> **Inspector graded this unit L1 — Light repair. The recorded findings
> price at $14,320 across 9 lines, which is at least L3 — Heavy turn /
> remodel. Two lines have no researched figure, so the real total is
> higher.**

It states both, names the gap, and picks nothing. Note it says *"the
recorded findings price at"*, not *"the unit needs"* — the budget is a
claim about what was written down, and the whole reason the grade might
be right is that the walk may have missed something.

### Surfaces

| surface | what it shows |
|---|---|
| unit page header | the grade beside the status, both by label |
| assessment page area list | grade per unit; a disagreement marker only where one exists |
| capex PDF / XLSX summary block | the sentence, beside the coverage sentence, only when they disagree |
| the budget line items | **nothing.** The grade is not a line and must never look like one. |

Agreement shows the grade and says nothing else — same as
`override_agrees` returning `note: None`. A tool that comments on
agreement teaches people to skip its comments.

---

## 5. Is the grade worth having at all?

Both sides, honestly, because this is the actual decision.

### For

**It is recorded at the moment of maximum information.** The roll-up
knows only what got written down. The inspector saw the whole unit, and
some of what makes a unit a gut job is not on any checklist — smell,
layout, the cumulative tiredness of everything at once. A grade captures
judgment that our items structurally cannot.

**Disagreement is diagnostic, whichever way it falls.** Inspector says
L1, findings price at $14k → either the grade was optimistic or the walk
under-recorded, and both are worth knowing. That is the same argument as
the T12 reconciliation gate, which `underwriting_property` explicitly
cites.

**It is one tap, and it is the first question anyone asks.** "What kind
of turn is 214?" is asked before "what is on the list".

**It can be validated against reality later.** When a unit is actually
turned, the invoice checks the grade. The roll-up is harder to score that
way because it is a construct with known holes.

### Against

**It is a second source of truth for a number that matters.** The moment
anyone budgets from grades rather than lines, the researched costs stop
being load-bearing. Section 4's "never substitutes" rule is a guard
against that, and guards erode.

**It goes stale inside a single visit.** A grade is formed early or
recalled at the end; findings accumulate throughout. Nothing prompts a
revisit, so the recorded grade is a snapshot of a partial walk presented
with the authority of a summary.

**Three bands is lossy in the direction we already solved.** The roll-up
gives dollars. Compressing dollars into three buckets and then reporting
a disagreement with the thing it was compressed from is a manufactured
argument — most "disagreements" near a boundary are rounding.

**Michelle's stated constraint points the other way.** *"Don't worry
about calculating paint, we just need to determine the conditions"* is a
request for **less** to fill in.

**The strongest objection: the computed side is usually absent.** Rates
will never be measured, several items are unpriced, and the whole
mechanism only produces a comparison when a unit has priced lines. A
disagreement feature whose computed half is frequently "cannot derive" is
mostly ceremony — and ceremony that asks the inspector for one more tap.

### Recommendation

**Worth having as a triage field, not as a budget input, and not yet.**

- *As triage* it earns its keep immediately: which units to walk first,
  which to price hard, what to tell an asset manager before the export
  exists. That use needs no agreement with the roll-up at all.
- *As a budget input* it should never be used, and section 4's rule
  should be written as a constraint rather than a convention.
- *Not yet*, because the derived half is thin. The honest sequence is
  **coverage first, grade second**: price the items in `UNPRICED` that
  scope detail would unblock, and the comparison becomes real. Shipping
  the disagreement machinery while it mostly answers "cannot derive"
  spends the inspector's attention and teaches them the notice is noise.

If it ships before then, ship the **field and the display only** — record
the grade, show it, derive nothing — and add the comparison when the
budget can carry it. That is a much smaller change and it forecloses
nothing.

---

## What this does not answer

- **The band thresholds.** Michelle's numbers. Indicative figures above
  are placeholders and must be labelled as such.
- **Whether a building-level grade is wanted.** Paresh grades units. A
  property roll-up of unit grades is a different claim needing its own
  derivation.
- **Whether the grade should be revisitable mid-walk**, and whether a
  grade recorded before half the findings should be flagged as stale.
