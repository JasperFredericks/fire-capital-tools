# Detail values for scope of work — design

**Design only. No schema change, no migration, no code. Written 2026-08-19
against master at `805bb9d`.**

> **Still open, and unaffected by what has shipped since.** `8b8ba17`
> closed the *choice-item* budget gap this document flagged in section 3
> — a missing alarm now produces a line. It did nothing for the problem
> this design addresses, which is *condition* items whose condition does
> not say which job. The two populations were always disjoint, and the
> shipped fix drew exactly that line: `WORK_OPTIONS` answers "does this
> option value mean work", never "which job".
>
> Two corrections to the text below, both marked in place: section 3's
> "one thing still open" is closed, and section 6's grouping-key
> recommendation has already shipped. Section 4's reasoning about
> `closet` and `dryer_vent` was re-checked against the code after the
> merge and holds unchanged.

The problem this solves, in one line: **our five conditions say how bad a
thing is, and never say which job fixes it** — so `closet` and
`dryer_vent` sit in `UNPRICED` not because nobody researched them but
because "a closet" is not one job, and `walls_ceiling` carries a
per-square-foot rate that will never be totalled because nobody will ever
measure a room.

---

## 1. The claim, and why `detail` is already the right column

`site_dd_findings.detail` exists and is populated today, but only on
`KIND_CHOICE` items: `flooring_type` holds `carpet`, `smoke_alarm` holds
`missing`, `appliance_dishwasher` holds `hookup_only`. Its schema comment
calls it "a categorical fact about the item that is NOT a condition".

The proposal is a **second population in the same column**:

| | written on | answers | example |
|---|---|---|---|
| **presence detail** (exists) | `KIND_CHOICE` items | *what is it / is it there* | `flooring_type` = `carpet` |
| **scope detail** (new) | `KIND_CONDITION` items, only when the condition is in `WORK_CONDITIONS` | *which job* | `toilet` = `replace_seat` |

**These two populations cannot collide, and the reason is structural, not
a convention anyone has to remember.** `kind` partitions the item set: an
item key is `KIND_CHOICE` or `KIND_CONDITION`, never both, and `_item()`
already enforces the corollary that only a choice item can carry
`with_condition` as an independent fact. So for any given `item_key`,
`detail` means exactly one thing. No schema change, no discriminator
column, no namespacing.

The validation gate is also already there and already correct:

```python
"detail": raw_detail if uc.is_valid_option(item, raw_detail) else None,
```

`is_valid_option` reads `item["options"]`. Giving a condition item an
`options` tuple makes this line validate scope details with no edit. The
only thing that has to change to admit them is that `_item()` currently
forces `with_condition` to `kind == KIND_CONDITION` and expects no
`options` on a condition item. That is one boolean, and section 7 says
where it sits in the order of work.

---

## 2. Which of Paresh's 25 scales encode a real scope distinction

I read all 35 choice lists in `The_View_Inspection_XLSForm_v7.xlsx`
(sha256 `16abb120…`, matching the manifest) and every survey field that
references them. The manifest's "roughly 25 item-specific condition
scales" is right. **The honest count of those carrying a real scope
distinction is nine.** Most of the rest are his phrasing of our five
states, at coarser resolution.

### The nine that carry a real scope distinction

| his list | the value that is a scope, not a severity | why it is a different job | our item | our reference cost today |
|---|---|---|---|---|
| `closet_condition` | Replace Rod / Replace Shelves / **Replace Rod and Shelves** | three jobs, three prices, one condition word | `closet` | **UNPRICED** |
| `toilet_condition` | **Replace Seat** vs Replace | a seat is tens of dollars fitted; a toilet is $600 | `toilet` | $600 each |
| `bathtub` | **Resurface** vs Replace | reglazing is a few hundred; replacement is a demolition | `tub_shower` | $3,275 each |
| `ceiling_wall_condition` | **Paint** vs **Repair and Paint** | drywall repair before paint is a second trade | `walls_ceiling` | $5.75/sqft |
| `door_condition` | **Paint** vs Repair vs Replace | repaint, rehang, or new slab and frame | `entry_door` | $1,450 each |
| `disposal_condition` | **Jammed** vs Not Working | a jam is a service call; a failure is a new unit | `appliance_disposal` | $375 each |
| `appliance_service` | **Service** vs Replace | a service call on a washer is not $925 | `washer`, `dryer` | $925 each |
| `vent_condition` | **Clogged** vs Missing | cleaning a dryer vent is not installing one | `dryer_vent` | **UNPRICED** |
| `hardware_condition` | **Loose** vs Replace vs Missing | tightening is minutes; a new lockset is not | `entry_door` | $1,450 each |

Two of the nine — `closet` and `dryer_vent` — are **already in
`UNPRICED`**, and reading the nine as a group explains why. They were not
skipped for lack of research. They were skipped because the reference
table's shape demands one canonical job per item and neither item has
one. Scope detail is the missing thing, not a missing price.

> **Re-checked after `8b8ba17`, and it holds unchanged.** Both are still
> `KIND_CONDITION`, still carry no options, and are still `UNPRICED` with
> their reasons untouched: *"no source separates them"* for `closet`,
> *"the checklist item does not distinguish them"* for `dryer_vent`. The
> work-options fix admitted **choice** findings to the budget and left
> condition items exactly as they were — so it could not have helped
> these two, and did not. If anything the merge sharpens the argument:
> the choice-item gap is closed, and what remains unpriced is precisely
> the set of items whose *condition* cannot say which job. That is this
> design's whole subject.

### Direct evidence that Paresh wanted this and his tool would not give it to him

`disposal_condition` has **two options with the same stored `name`**:

```
replace | Jammed
replace | Not Working
good    | Good
na      | N/A
```

He wrote two labels he wanted to distinguish and was forced to store one
value for both. Any submission recording "Jammed" is indistinguishable in
the data from one recording "Not Working". That is a defect in his form —
but it is also the clearest available statement of the requirement: he
wanted to record *the observation* while storing *the scope*, and XLSForm
gave him one field. `detail` alongside `condition` is precisely the two
fields he did not have.

### The sixteen that are our five states in his words

`condition_pag` (Poor/Acceptable/Good), `flooring_condition`,
`fan_condition`, `appliance_condition`, `kitchen_item`, `bath_item`,
`mech_condition`, `thermostat`, `detector`, `fire_ext`, `light_fixtures`,
`bath_leaks`†, `mold_options`, `pest_evidence`, `pest_type`,
`flooring_type`.

Nothing to import. The last four we already imported verbatim in an
earlier run; `flooring_type` we already had; the rest collapse onto
`repair`/`replace`/`good` plus a `missing` state our `PRESENCE` and
`ALARM_STATES` sets already carry. Several are strictly *less* expressive
than ours — his `thermostat` scale has two values where we have five, and
his `condition_pag` has three.

† `bath_leaks` (No / Bathtub / Sink / Both) is not a severity scale at
all — it records **which fixture** leaks. That is real information on a
different axis from our `visible_leaks` (none / minor / active), which
records severity. Both are defensible; neither subsumes the other. It is
a candidate for a later run, not for this design, because it is a new
item rather than a detail on an existing one.

### Four that are real, and are NOT detail values

Recorded here so a later run does not re-derive them:

- **`classic_reno`** (Classic / Partial Reno / Renovated) — a **room-level
  finish grade**, asked once per room across eleven rooms. Not an item
  attribute. Genuinely new information we have nowhere to put.
- **`bathroom_type`** (Full / Half) — a **room subtype**. It matters
  because our `BATHROOM` extras ask `tub_shower` in every bathroom, and a
  half bath does not have one, so we currently invite an inspector to
  rate a fixture that is not there. Belongs with `ROOM_TYPES`.
- **`unit_grade`** (L1 / L2 / L3) — the turn grade. Explicitly deferred to
  its own run.
- **`balcony_condition`** (Good / Repair / **Safety Concern**) — "Safety
  Concern" is an **urgency**, not a scope. It says *when*, not *what*.
  Forcing it into scope detail would be the same category error as
  forcing "missing" onto a wear scale, which is the error this whole
  design exists to avoid.

---

## 3. GFCI present-but-not-tripping: it does not fit the detail pattern

**It does not, and it does not need to. It is already handled, as an item
option.**

The note in `site_dd_unit_checklist.py` — "there IS a real difference, and
it is a `detail` question rather than a new item" — was **written before
the change and is now stale.** The live checklist reads:

```python
_item("gfci", "GFCI outlets", KIND_CHOICE,
      (("present", "Present & working"), ("not_working", "Present, not working"),
       ("absent", "None")),
      hint="Required within reach of the sink.", with_condition=False)
```

`not_working` is the dangerous case — an outlet that is there, that looks
right, and that does not trip. It is captured, in the kitchen and in every
bathroom, as **presence detail on a choice item**. The mechanism it uses
is the one that already existed.

**Why it could not have been scope detail.** Scope detail as defined here
is written only when `condition ∈ WORK_CONDITIONS`. `gfci` is
`KIND_CHOICE` with `with_condition=False`: it has no condition at all, and
therefore no gate to hang a scope on. More importantly the fact being
recorded is not *which job* — it is *what is true about the device*. That
is presence detail by definition.

**The one thing still open, and it is a cost-table question rather than a
checklist one.** `gfci` is priced at $195 each, a device replacement.
That price is right for `absent` and right for `not_working`, and both
need an electrician. But `gfci` has no condition, and **`build_lines`
only ever sees findings filtered by condition**:

```python
work = [f for f in findings if f.get("condition") in cond.WORK_CONDITIONS]
```

So a GFCI recorded as `not_working` — the dangerous case, correctly
captured — **cannot reach the capital budget at all**, because it has no
condition and never will. The same is true of every `with_condition=False`
choice item: a missing smoke alarm, an absent range, an active leak.
`smoke_alarm_unit` is priced at $260 and `co_alarm` at $195, and neither
can ever produce a budget line.

That is a real gap, it is larger than GFCI, and it is **not** solved by
detail values. It needs a rule saying which *presence detail* values are
themselves work — `missing`, `absent`, `not_working`, `active` — and
letting them into `work` alongside the two conditions. I am flagging it
rather than designing it, because it changes what reaches Underwriting
and deserves its own decision. Nothing in sections 4–6 depends on it.

> **CLOSED — `8b8ba17`, 2026-08-19.** Investigated in
> `docs/site-dd-work-options-gap.md` and fixed as described there. The
> rule landed close to the guess above but keyed by **option set** rather
> than by value, because the same string means opposite things in
> different sets: `present` is work on `mold` and not on an appliance,
> `none` is work on `egress_window` and not on `visible_leaks`. A flat
> list of `missing / absent / not_working / active` would have been wrong
> on four items.
>
> Verified on production: `gfci` recorded `not_working` produces one line
> at $195.00 under Mechanical, Electrical & Plumbing. It reaches
> Underwriting no differently than before, because `to_capex_lines()`
> still has no live callers — that is a separate open item.

---

## 4. The reference cost table, which assumes one canonical job per item

### The generalisation already exists, in code, tested

`for_item` is already detail-aware for exactly one item:

```python
def for_item(item_key: str, flooring_type: str | None = None) -> ReferenceCost | None:
    if item_key in ("flooring",) and flooring_type:
        rate = FLOORING_BY_TYPE.get(flooring_type)
        if rate:
            return _c("flooring", rate, UNIT_SQFT, FLOORING_SOURCES, ...)
        return None if flooring_type in FLOORING_BY_TYPE else REFERENCE_COSTS.get("flooring")
    return REFERENCE_COSTS.get(item_key)
```

`FLOORING_BY_TYPE` prices carpet at $3.50 and tile at $13.50 — a **3.9x
spread on one item key**, resolved by a detail value, with a fallback to
the item-level entry when the detail is absent. That is the entire design
being proposed, already running in production, already exercised by tests.

The proposal is to **stop special-casing `flooring` and make the second
argument what it always was**:

```python
COST_BY_DETAIL: dict[tuple[str, str], ReferenceCost] = { ... }

def for_item(item_key: str, detail: str | None = None) -> ReferenceCost | None:
    if detail:
        specific = COST_BY_DETAIL.get((item_key, detail))
        if specific is not None:
            return specific
    return REFERENCE_COSTS.get(item_key)
```

`FLOORING_BY_TYPE` folds into `COST_BY_DETAIL` as seven `("flooring", …)`
entries and stops being a special case. **The fallback is the whole
migration story** — see section 5.

### One call site is already looking in the wrong place

```python
# site_dd_capex_export.py:138
known = refcosts.for_item(f.get("item_key"),
                          f.get("detail") if f.get("item_key") == "flooring"
                          else None)
```

A `flooring` finding is `KIND_CONDITION`. **Its `detail` is always NULL** —
the material lives on the sibling `flooring_type` row, which is why
`site_dd.py` builds `flooring_by_room` from that sibling. So this
expression passes `None` on every call it was written for.

It is **not a live bug**, because every flooring variant is `sqft` and
this call only reads `.unit`. But it is correct by accident, and it
becomes a real bug the moment any item has detail-dependent *units*.
Under this design it simplifies to `refcosts.for_item(f.get("item_key"),
f.get("detail"))` and starts being right for the reason it looks right.

`flooring` stays the one item whose pricing detail lives on another row.
That asymmetry is worth keeping — the material is a fact about the floor
whether or not it needs replacing, so it belongs on its own item — but it
should be **named** in the code rather than left to be rediscovered.

### The Step E constraint, designed in

Michelle: *"don't worry about calculating paint, we just need to determine
the conditions."* No floor area will ever be recorded. The Part 28 audit
found the resulting message — "Needs a measured floor area before it can
be totalled" — sound but prescribing an action nobody will take.

**The design constraint that follows: a scope detail must never be the
thing that makes a line need a measurement. It must be able to move a
line off the rate path.**

Concretely, `walls_ceiling` at $5.75/sqft can never be totalled. But:

| finding | resolves to | totals? |
|---|---|---|
| `walls_ceiling`, `repair`, no detail | $5.75/sqft | no — needs an area |
| `walls_ceiling`, `repair`, `paint_only` | a per-room job price, `each` | **yes** |
| `walls_ceiling`, `repair`, `repair_and_paint` | a higher per-room job price, `each` | **yes** |

**This is the strongest argument for the whole feature.** Naming the job
is something an inspector will do standing in the room. Measuring the
floor is not. Scope detail is the mechanism that lets a rate item be
priced without a measurement — it converts an unanswerable question into
one that has already been answered by the person holding the tablet.

Two honesty requirements on that, which must not be skipped:

1. A per-room `each` price is a **typical-room** figure. Its `note` must
   say so and name the room size it assumes, the way every other entry
   names its range and its midpoint. It is a different kind of number
   from $600 for a toilet, and the reference sheet must not present the
   two as the same kind of claim.
2. `is_rate(unit)` must be read off the **resolved** entry, not the item.
   `site_dd_capex_export` currently comments that "the unit belongs to the
   item, not to who priced it" — under this design the unit belongs to the
   **(item, detail)** pair, because that is what identifies the job.
   `flooring` is the proof it was always so: every flooring detail happens
   to be `sqft`, which is what let the current wording pass.

### Where the new prices come from

`COST_BY_DETAIL` needs figures, and this design does not invent them.
Every entry needs the treatment the existing 36 got: named sources, a
stated range, a midpoint, a date. The nine items above imply roughly
fourteen new entries. That is a research task for its own run, and **the
design works with zero of them present on day one** — a `(item, detail)`
pair with no entry falls back to the item-level price, which is exactly
today's behaviour.

---

## 5. Existing findings with no detail stay valid — by construction

Assessment 11 — Michelle's live walk at Nabob Hill — has **23 findings**
(read read-only; content fingerprint `11fdd001f2fca08e`). Of those:

> **That fingerprint is RETIRED as of 2026-08-20.** Its algorithm was
> never recorded, so it cannot be reproduced and could never have
> detected a change. The replacements, with their algorithms stated,
> are in HANDOFF: data fingerprint `f6451ecb366f6ab4`, export content
> hash `d0b8436a3998f63b`. The 23-finding breakdown below is unchanged
> and was re-confirmed against production on 2026-08-20.

- **1** has a detail: `flooring_type` = `carpet`. Presence detail. Untouched.
- **2** have a condition: `flooring` = `good`, `walls_ceiling` = `repair`.
- **20** have neither.
- **Exactly one** row is in `WORK_CONDITIONS`: `walls_ceiling` = `repair`.

So the entire migration question reduces to one row, and its answer is the
fallback: `for_item("walls_ceiling", None)` → $5.75/sqft, the same
`ReferenceCost` object it resolves to today. **Same price, same unit, same
sources, same "needs a measured floor area" message.** The line does not
move.

**The rule is that absent scope detail means "the default job for this
item at this condition", and the reference table's item-level entry is
already the price of that default job.** That is not a compatibility shim
bolted on afterwards — it is what `REFERENCE_COSTS` has always meant. The
36 entries each price the canonical job. Detail names a non-canonical one.

Four consequences worth stating, because "stays valid" is easy to assert
and cheap to get wrong:

1. **No backfill. No migration. No default value.** `detail` is already
   `TEXT` with no `NOT NULL` and no default. Every existing row is already
   in the state the new code expects.
2. **A stale detail cannot poison a price.** `is_valid_option` rejects on
   write and `COST_BY_DETAIL.get()` misses on read, so if an option is
   removed from an item later, findings holding the removed value fall
   back to the item price rather than erroring — the same tolerance
   `is_known_item` already gives stale item keys.
3. **Completion percentage does not move.** `summarize_unit` counts only
   conditions and explicitly excludes choices. Scope detail is optional and
   is never a condition, so it cannot change any denominator. A unit that
   reads 40% complete today reads 40% after.
4. **Re-opening an existing finding must not demand a detail.** The form
   renders from stored values; a `repair` with no detail must render with
   no option selected and save back unchanged. Every option set therefore
   needs an explicit unselected state, and `site_dd_area.html` already has
   the pattern:
   `name="detail_{{ item.key }}" value="" {% if not (row and row.detail) %}checked{% endif %}`.

---

## 6. How a detail value reaches the exports

**Both exports read one field.** The XLSX writes `l["label"]` into its
first column; the PDF's item rows read the same key. `detail` is not in
the line dict at all today.

### The grouping key, which is accidentally safe

```python
key = (f.get("area_id"), f.get("room_id"), f.get("item_key"),
       f.get("condition"), described["cost"], described["source"],
       (f.get("instance_label") or "").strip())
```

`detail` is absent. Two toilets in one bathroom, one needing a seat and
one needing replacement, both recorded `replace`, would collapse into
**"Toilet ×2"** at whichever price came first.

In practice they will not, because **`described["cost"]` is in the key**
and the two details resolve to different costs — so once `COST_BY_DETAIL`
has entries, differing scopes separate themselves. But that safety is a
side effect, and it fails in exactly the case that matters most: **two
findings whose scopes differ and whose detail is not yet priced** both
carry cost `None`, share the key, and merge into one line that names one
job and hides the other. The unpriced case is where an inspector's words
are the only information there is, and it is the case that loses them.

**Add `f.get("detail")` to the tuple.** One element. It is the same
argument the existing docstring already makes for putting cost and
condition in the key — "nothing can be absorbed into a quantity unless it
is interchangeable with the rows beside it" — and two different jobs are
not interchangeable.

> **DONE — `8b8ba17`.** Shipped with the work-options fix rather than
> with this design, because it became load-bearing there first: admitting
> choice findings without it would have merged a missing alarm and one
> needing replacement into "Smoke alarm ×2", swapping a silent drop for a
> silent merge. The step remains correct for scope details and is now
> already in place for them. **Step 3 of section 7 is therefore half
> done** — the key is widened; the label suffix is not.
>
> A related piece also shipped and is worth knowing before implementing
> the suffix: lines now carry a **`state`** field, and both exports render
> it in the column that used to print `condition`. It resolves to the
> condition's label for a condition item and to the *option* label for a
> choice item. A scope-detail suffix has to compose with that field
> rather than around it — `state` answers "what is wrong", the suffix
> answers "which job", and those must not end up saying the same thing
> twice on one row.

### Composing the label, not adding a column

```python
"label": (f.get("instance_label")
          or labels.get(f.get("item_key"))
          or f.get("item_key")),
```

Recommendation: **suffix the scope detail's human label onto the item
label**, so `Toilet` becomes `Toilet — replace seat` and `Closet` becomes
`Closet — replace rod and shelves`. One change, in one function, and both
exports carry it.

Three reasons to prefer this over a `Detail` column:

1. **The PDF has no room for a column.** It is a fixed-width table on a
   portrait page; a tenth field means re-laying it out. The label column
   already sizes to its content.
2. **Presence detail must not be suffixed.** `flooring_type` is
   `NOT_A_COST_ITEM` and never reaches a budget line, and every other
   presence-detail item is `with_condition=False` and so never passes the
   `WORK_CONDITIONS` filter. Today the suffix would therefore apply only
   to scope details with no test needed — but that is a fact about the
   current filter, and if section 3's gap is closed later, presence
   details start arriving. **Gate the suffix on `kind == KIND_CONDITION`
   explicitly, now, rather than relying on the filter.**
3. `instance_label` already takes precedence over the item label and
   should keep it: a user who typed "Powder room toilet" said something
   more specific than the checklist knows. The suffix appends to whichever
   base wins, so a labelled instance reads
   `Powder room toilet — replace seat`.

### What does not change

- **The three-bucket summary and `coverage_sentence`** count lines by
  whether they are priced. More lines, same arithmetic, and the sentence
  stays true — it is one of the four messages the Part 28 audit passed.
- **The "Reference costs" sheet** lists `REFERENCE_COSTS` by key. It must
  gain the `COST_BY_DETAIL` entries, or a budget line will be priced from
  a figure the sheet meant to disclose and did not. That is a disclosure
  obligation, not a nicety — the sheet exists so a number in the budget
  can be traced.
- **`UNPRICED`** loses `closet` and `dryer_vent` once their details are
  priced, and their reasons — currently arguing that the item has no
  canonical job — become the explanation of why they needed details.

---

## 7. Order of work, if this is approved

1. `_item()` accepts `options` on a `KIND_CONDITION` item without forcing
   `with_condition`. Options on the nine items in section 2. No pricing.
2. Form renders the scope detail **only when the condition is in
   `WORK_CONDITIONS`**, always optional, with an explicit unselected
   state. Nothing else changes; nothing is priced differently.
3. `detail` into `build_lines`' grouping key; label suffix gated on kind.
   Exports now distinguish scopes even with no prices attached.
4. `for_item(item_key, detail)` generalised; `FLOORING_BY_TYPE` folded in;
   the `== "flooring"` special case at `site_dd_capex_export.py:138`
   removed. **Behaviour identical** while `COST_BY_DETAIL` is empty.
5. Research `COST_BY_DETAIL`, with sources and dates, starting with
   `walls_ceiling` paint-only — that is the one that converts an
   untotallable rate into a number.

Steps 1–4 are safe with no researched figures at all. Step 5 is where the
budget changes, and it should be a separate decision with the numbers in
front of Michelle.

---

## What this design does not answer

- **Presence details that are work never reach the budget** (section 3).
  Bigger than detail values; needs its own decision.
- **Where `classic_reno` and `bathroom_type` live** (section 2). Room-level
  attributes; we have nowhere to put them.
- **`bath_leaks` as leak location.** Real, on a different axis from
  `visible_leaks`, and a new item rather than a detail.
- **What a per-room paint price actually is.** Section 4 asserts the
  mechanism, not the figure.
