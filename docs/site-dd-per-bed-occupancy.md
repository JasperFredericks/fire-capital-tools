# Per-bed occupancy — scope, options and cost

**Design only. No build. Written 2026-08-19 against master at `805bb9d`.**

For a decision by Michelle. The recommendation is section 4; the cost is
section 5; **section 1 is the question that should be answered before
either is read**, because it can make the whole thing unnecessary.

---

## 1. Whose problem is this — the premise, checked first

The evidence for per-bed occupancy is `rent_roll.csv`, one of the four
files **Paresh Patel sent from Zuna Investments**. It describes **The View
at Pembroke**, which is Zuna's property, not Michelle's.

**Nothing in this repository establishes that Michelle owns a single
by-the-bed property.** The notetaker property registry holds twelve
entries; no by-the-bed rent roll has ever been uploaded to this
application; and the three MMR workbooks on disk are conventional
multifamily. So the honest statement of the requirement is: *a mature
instrument we were given, for somebody else's asset class, records
something ours cannot.*

That is a real signal — it is why the file was worth reading — but it is
not yet a requirement. **What the answer changes:**

| if Michelle's portfolio… | then |
|---|---|
| has no by-the-bed property | **build nothing.** Section 6 stands on its own and costs nothing. |
| has one or two | option B in section 3, at the section 5 cost |
| is materially student housing | option B, plus the ingest work, and the vocabulary question in section 2 becomes urgent rather than interesting |
| has by-the-bed units where **two beds share a bedroom** | option B does not work; option D, and roughly double |

The last row is the one that decides the shape rather than the size, and
it is a narrow factual question: *at any by-the-bed property you own, can
one bedroom be leased to two people?* At The View it cannot — every unit
is 4BR/4BA, one bed per bedroom with its own bathroom.

---

## 2. What is actually lost, in numbers

Read from the real file, all 84 rows, not a sample.

```
units: 84    beds: 336    vacant beds: 107  (31.8%)
bed states: Occupied 224 | Vacant Ready 72 | Vacant Not Ready 29 | Notice 6
unit-level `occupied` column: yes 76 | no 8
```

**The unit-level flag reports 8 vacant units — 9.5%. The beds report
31.8%.** The gap is not rounding:

> **38 of 84 units are marked `occupied=yes` and contain vacant beds. 75
> turnable beds — 70% of all the vacancy in the building — are invisible
> to a per-unit status.**

Recording this building the way Site DD records a unit today produces
"76 occupied, 8 vacant". A unit like 134 — three of four beds empty — is
indistinguishable from one that is genuinely full.

### The vocabulary does not fit, and that is a finding in itself

`AREA_STATUSES` is `('occupied', 'vacant', 'down')`. The file carries
four states, and they are not the same four:

| The View | our nearest | honest? |
|---|---|---|
| Occupied | `occupied` | yes |
| Vacant Ready | `vacant` | yes |
| **Vacant Not Ready** | `vacant`? `down`? | **no** — it is vacant *and needs a turn*, which is neither |
| **Notice** | `occupied` | **no** — occupied now, turnable on a known date |

`Vacant Ready` versus `Vacant Not Ready` is exactly the distinction a
site DD exists to establish, and we have nowhere to put it. `down` in our
vocabulary means off-line, which is a stronger claim than "not yet
turned". So this is not only a granularity problem — **the scale itself
is short by at least one state, and that is true for conventional
multifamily too**, independent of anything to do with beds.

### One data-quality fact the ingest must not paper over

**Five rows list three beds on a four-bedroom unit** (232, 511, 512, 534,
713). Every one of them is missing bed **A** specifically:

```
unit 511 declares 4 beds, lists 3 -> B:Occupied; C:Occupied; D:Occupied
```

Whether bed A is occupied-and-unlisted or does not exist **cannot be
determined from the file**. An ingest must record "unknown", not infer.
Filling it either way changes the vacancy figure and the turn budget.

Separately: `unit_sqft` reads **300** on a 4BR/4BA unit, which is not a
plausible unit area. It is very likely per-bed. That is a question for
whoever supplies the file, not something to resolve by assumption, and it
matters because any sqft-rate line would be wrong by 4x.

---

## 3. The options, worked through

### A — Widen `AREA_STATUSES`

Add `partially_occupied`, or add `notice` and a `vacant_not_ready`.

Cheapest by a wide margin: one tuple, one form control, no migration
(`status` is already a nullable TEXT column that validates against the
tuple and writes NULL otherwise, so old rows are untouched).

**But it does not answer the question.** "Partially occupied" cannot say
*two of four*, cannot say *which two*, and cannot carry a finding. Unit
134 and unit 123 both become "partially occupied" when one has three beds
to turn and the other has one. You cannot budget a turn from it.

It also breaks a consumer by design: `AREA_STATUSES`' own comment says it
*"drives Site DD Lite in a later branch, which inspects vacant units and
common areas only"*. A partially-occupied unit is neither in nor out of
that filter, and the branch does not exist yet to make the call.

**Worth doing anyway, and separately**, for the `Vacant Not Ready` /
`Notice` gap in section 2 — that is a real shortfall in the per-unit
vocabulary regardless of beds. It is not a substitute for B.

### B — A bed is a room, and we already model rooms *(recommended)*

`site_dd_rooms` exists, carries `room_type` (including `bedroom`),
`label`, and `sort_order`, and **findings are already keyed on
`room_id`**. At The View, a bed *is* a bedroom — 4BR/4BA, one occupant
per bedroom, each with its own bathroom. So per-bed occupancy is a
`status` column on `site_dd_rooms`, and the four beds are the four
bedrooms the inspector already walks.

Everything composes:

- The walk order is unchanged — bedrooms are already in it.
- A finding recorded in bedroom 2 is already attributable to bed B; no
  new join, no new key.
- Room labels already exist, so "Bed A" is a label, not a schema change.
- The unit's status becomes derivable — *3 of 4 occupied* — rather than
  separately entered and able to disagree.

And it generalises past student housing: a room-level status is also how
you record that one bedroom is sealed off for water damage while the
tenant lives in the rest of the unit, which is conventional multifamily.

**The cost of being wrong** is the shared-bedroom case. If two beds can
share one room, a room status cannot represent them and this has to
become option D. That is section 1's question.

### C — A field on the unit holding the packed string

Store `"A:Occupied; B:Vacant Ready; …"` in a text column on the area.

**Rejected.** It is the same mistake the source file makes — structured
data packed into one column — and this project has already written down
why that is not acceptable: the `resident_name` column in that very file
holds bed states rather than names, and the manifest flags it as
something to design around rather than copy. A packed string cannot be
queried, cannot be counted, cannot carry a finding, and would have to be
parsed again at every read.

Listed because it is the cheapest thing that *looks* like a solution, and
somebody will propose it.

### D — A `site_dd_beds` table

A real sub-area: `bed(id, room_id or area_id, label, status)`.

The most general answer and the only one that survives shared bedrooms.
It is also a new table, a new set of readers, new form handling, a new
level in the roll-up, and a second thing a finding could attach to — and
that last point is the expensive one, because `site_dd_findings` would
gain a `bed_id` and every query, export and grouping key that currently
reasons about `(area, room, item)` acquires a fourth dimension.

**Do not build this unless section 1's shared-bedroom question comes back
yes.** It is roughly double option B and most of the extra buys
generality nobody has asked for.

### E — Do nothing

The right answer if no property in the portfolio is leased by the bed.
Section 6's independent findings still stand.

---

## 4. Recommendation

**Option B, conditional on section 1, with option A taken separately and
now.**

A is worth doing on its own merits — `Vacant Not Ready` and `Notice` are
missing from a per-unit vocabulary that conventional multifamily also
needs — and it is small. It should not wait on the bed question and
should not be sold as answering it.

B should not start until the shared-bedroom question is answered, because
the answer decides between B and D and rework between them is most of the
build.

---

## 5. Cost

Estimated by measuring comparable changes already made in this codebase,
rather than by feel. The nearest analogue is `sitedd-cost-unit-toggle`
— a new stored per-row value, a form control in both capture templates,
downstream logic and tests: **244 lines across 7 files**, one build run.

### Option A — widen the status vocabulary

**~60–100 lines, 4–5 files. Well under one run; a step within a run.**

`AREA_STATUSES` plus labels, the two form controls that already render
statuses, and tests. **No migration**: `create_area` already writes NULL
for any status outside the tuple, so existing rows are untouched and new
values simply become selectable.

### Option B — per-bed occupancy as room status

**~300–450 lines across 8–10 files. One build run**, the same size as a
typical Site DD merge this session.

| piece | size | note |
|---|---|---|
| `site_dd_rooms.status` + migration | small | a **third** use of the existing `_ADDED_COLUMNS` pattern, proven twice on findings and media. Nullable ALTER, no table rebuild. |
| `ROOM_STATUSES` vocabulary | small | shares section 2's four-state question with option A |
| form control, room page + area room list | medium | mirrors the area status control that already exists |
| `create_room` / `save_room` accept it | small | |
| roll-up: beds occupied / vacant / to-turn per unit and per assessment | **medium–large** | the real work, and the part that varies |
| report and export surfacing | medium | |
| tests, incl. the `copy_layout` guard below | medium | |

The range is honest, and the roll-up is what moves it. **What narrows
it:** deciding whether the unit's own `status` becomes *derived* from its
beds or stays independently entered. Derived is more correct and touches
every existing read of `area["status"]`; independent is cheaper and
permits a unit marked occupied whose beds are all vacant — the exact
class of contradiction this work exists to remove. That single decision
is most of the spread between 300 and 450.

### The ingest — priced separately, and the premise needs correcting

**Site DD has no rent-roll upload.** Verified against the route table:
areas are created one at a time by a form on the assessment page. So this
is not "changing what the seeding does" — **there is no seeding**, and
building it is a separate piece of work larger than option B itself.

What exists is `underwriting_rentroll.parse_rent_roll_workbook()`, and it
would not read this file:

- It is **ResMan `.xlsx`**, column-driven, and requires `Unit`, `Market
  Rent` and `Description`/`Amount` charge lines. The View's file is a
  **KoboToolbox `pulldata` CSV** with none of those.
- It raises `UnrecognizedRentRoll` rather than guessing — correctly, and
  it already carries a named message for the MMR mistake.

**Whether a ResMan export for a student property carries per-bed
occupancy at all is unknown, and I will not assume it.** No such file is
in the repository or on this machine. If ResMan exposes beds as separate
unit rows (`511-A`, `511-B`), the ingest is nearly free and option B gets
its data for almost nothing. If it exposes them packed like The View's
file does, it needs a parser and the five-missing-A problem in section 2
becomes a live correctness question. **One real rent roll from a
by-the-bed property she owns collapses this uncertainty**, and that is
the single highest-value thing to ask for.

Until then, an ingest estimate would be a number with nothing behind it,
and this document does not give one.

---

## 6. What breaks, and what does not

**Existing per-unit findings stay valid, and more cleanly than in the
detail-values design.** Occupancy would live on `site_dd_areas` /
`site_dd_rooms`; findings live in `site_dd_findings`. **Different
tables.** Nothing about a finding is read, written or filtered by
occupancy. Assessment 11's 23 findings are untouched by construction, not
by a compatibility rule.

**`copy_layout` is already safe, and needs a test to stay that way.** It
copies *"only room_type, label and sort_order"* — an explicit column
list, so a new `status` column is excluded by default. That default is
exactly right: occupancy is a fact about one unit on one day and must
never travel with a layout. But it is safe by an enumeration somebody
could later "tidy" into `SELECT *`, so it wants a test asserting a copied
room comes back with no status.

**Completion percentage is unaffected.** `summarize_unit` counts
conditions on findings. A room status is neither.

**Site DD Lite does not exist yet** and is the one real consumer to
think about. Its stated purpose — inspect vacant units and common areas
only — becomes *better* under option B, not worse: it can select the
vacant **beds** in an occupied unit, which is precisely the walk a
student-housing turn actually is. Under option A it becomes ambiguous.
That is an argument for B over A on the merits, not just on granularity.

---

## 7. Does the detail-values work interact?

**Barely, and the one place it touches is a reason to sequence them, not
to merge them.**

They are orthogonal by construction: detail values are per-*finding*
scope-of-work on an item; occupancy is per-*area* or per-*room* state.
Different tables, different keys, no shared code path. Neither blocks the
other and they can be built in either order.

Two genuine points of contact:

1. **They compete for the same capex line.** A turn budget wants "bed C:
   flooring, replace, carpet — $3.50/sqft" — the scope from detail
   values, the *where* from bed status. The grouping key in
   `build_lines` is already `(area_id, room_id, item_key, …)`, so if a
   bed is a room, per-bed budgeting is **already keyed correctly** and
   needs the detail work to be useful rather than the other way round.
   Detail values first is the better order.

2. **Both are cases of "our vocabulary is shorter than the world's".**
   Detail values found five states where Paresh has a scope; this found
   four occupancy states where we have three. The lesson generalises and
   belongs in the handoff: **when a mature instrument records more states
   than we do, check whether ours is a simplification or an omission.**
   `Vacant Not Ready` is an omission.

And one thing that is **not** an interaction, stated because it looks
like one: the `with_condition=False` capital-budget gap (the companion
document) is independent of both. It would drop a missing smoke alarm in
bed C exactly as it drops one in a conventional unit.
