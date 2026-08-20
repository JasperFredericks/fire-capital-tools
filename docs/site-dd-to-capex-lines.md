# `to_capex_lines()` and the dead-reader glob

**Investigation. No build. Written 2026-08-20 against master at `5cde052`.**

Two questions, one decision each. The second corrects a claim I made in
`docs/site-dd-work-options-gap.md` that turns out to be wrong.

---

## 1. What `to_capex_lines()` was for

**Built for a Site DD → Underwriting hand-off that was designed, tested
end to end, and never wired to anything. It has since rotted: three
defects that were found and fixed in the export were never fixed here,
so connecting it as it stands would put a known bug into the
underwriting model.**

### Provenance

It arrived whole in `c39bbce`, *"site-dd: cost provenance columns and
manual estimates"*, 2026-08-14. That commit message describes it as
finished work:

> **THE CAPEX HAND-OFF NOW HAS A TEST, AND IT FOUND TWO GAPS**
>
> `to_capex_lines()` maps findings onto `underwriting_capex_lines` and is
> verified end to end through the real writer and the real roll-up.

So it is not an abandoned sketch. It maps findings onto
`underwriting_capex_lines` rows, writes `source = CAPEX_SOURCE`
(`"site_dd"`), carries `source_ref` back to the originating finding id,
and has a `SCOPE_MAP` translating Site DD's property/unit/room onto the
`exterior|interior` vocabulary that table silently coerces. Four test
files exercise it.

**What was never written is the caller.** There is no route, button or
form that turns an assessment into underwriting capex lines. Its only
three mentions outside its own module and tests are prose in comments —
twice in `site_dd_capex_export.py`, once in `site_dd_db.py` — which is
precisely the failure mode the dead-reader sweep was rewritten as an AST
walk to stop being fooled by.

`underwriting_capex.SOURCE_SITE_DD` is the matching half of the same
unfinished join: defined, counted by `summarize()`, written by nothing.

### It was not superseded — the two mappers have different destinations

`build_lines()` in `site_dd_capex_export.py` looks like a replacement and
is not:

| | `to_capex_lines()` | `build_lines()` |
|---|---|---|
| destination | rows in `underwriting_capex_lines` | the PDF and XLSX |
| consumed by | the underwriting model — equity, IRR, DSCR | a person reading a document |
| status | no caller | wired, exercised on production |

A document and a model are different products. Nothing else writes Site
DD findings into Underwriting, so the job it was built for is still
undone.

### But it has rotted, and this is the decisive part

Every correctness fix the export received since August was applied to
`build_lines()` alone. Measured by running both mappers over identical
findings:

**A rate item — `walls_ceiling`, repair, priced from the reference table
at $5.75 *per square foot*:**

```
to_capex_lines  -> qty=1.0  unit=$5.75  line_total=$5.75
build_lines     -> qty=None unit=$5.75 per sq ft  total=None
                   "Priced at $5.75 per sq ft. Needs a measured floor
                    area in square feet before it can be totalled..."
```

> **The quoted message was reworded on 2026-08-20** (Part 38 Step A). It
> now reads "Priced by scope, not by this walk...", because the old
> wording prescribed a measurement no route can record. The comparison
> above is unchanged in substance -- the point is `qty=1.0` versus
> `qty=None` -- and is left as it was run.

That is **exactly the bug `b613a76` fixed**, the one HANDOFF calls "the
most consequential change here" — a rate multiplied by an instance count,
producing a repaint budget of $5.75. `to_capex_lines()` sets
`quantity = len(rows)` and leaves `total_cost` to
`underwriting_capex.line_total()`, which multiplies quantity by unit
cost. Seven of the thirty-six researched figures are rates and they are
the expensive ones.

**Two toilets at different prices, same room:**

```
to_capex_lines  -> 1 line: qty 2 @ $450.00 = $900.00
build_lines     -> 2 lines: qty 1 @ $450.00, qty 1 @ $600.00
```

$300 leaves the budget without a trace. `build_lines()`' docstring names
this as the reason condition, cost and provenance joined its grouping
key; `to_capex_lines()` still groups on `(area, room, item)` and takes
the first non-null cost it finds.

**Two alarm states, same room, same price:**

```
to_capex_lines  -> 1 line
build_lines     -> 2 lines: ['Missing', 'Needs replacing']
```

The silent merge closed by `8b8ba17`, still open here.

### The decision this evidence supports

**Neither delete nor connect as-is.** Deleting discards a real design —
the scope mapping, the `source_ref` back-link and the coercion guard are
all knowledge that would have to be rediscovered. Connecting it ships the
rate bug into equity and IRR, which is strictly worse than the status quo
where, as HANDOFF records, *"Site DD capex does not reach Underwriting,
so the rate bug never touched equity, IRR or equity multiple."*

The shape that follows from the evidence is: **when the hand-off is
wired, it should be wired to `build_lines()`'s output**, with
`to_capex_lines()` reduced to the part that is genuinely its own —
mapping a budget line onto an `underwriting_capex_lines` row, including
the scope translation and the `source_ref`. One grouping, two
destinations. Two independent groupings of the same findings is how they
drift, and this pair has already drifted three times.

Not done this run. Recorded so the next person does not have to
re-derive it.

---

## 2. Widening the dead-reader glob: measured, and the answer is no

### First, a correction

`docs/site-dd-work-options-gap.md` says:

> Widening the file glob would have found the fifth instance.

**That is wrong.** The sweep is gated on *two* things, not one: the glob
`tools/*_db.py` **and** a name prefix.

```
READER_PREFIXES = ('list_', 'get_', 'fetch_', 'find_', 'count_',
                   'search_', 'load_', 'read_')

to_capex_lines  matches a reader prefix?  False
```

`to_capex_lines` starts with `to_`. Widening the glob alone would not
have found it, and neither would widening it now. Catching it needs the
prefix list widened too — a far larger change, because "every public
function in `tools/`" is a different and much noisier question than
"every reader".

I asserted the glob would have caught it without checking the prefix
gate. That is the same error the standing rule already names: a claim
that holds for the case that prompted it, not checked against the
mechanism.

### The measurement

Run with the sweep's own machinery — its `READER_PREFIXES`,
`production_sources()` and `called_names()` — changing only the glob, so
what is measured is the real instrument rather than a re-implementation.

```
current glob  tools/*_db.py : 64 readers, 2 with no caller (2 allowlisted)
widened glob  tools/*.py    : 90 readers, 9 with no caller

NEW HITS the widening would produce: 7
```

Classified one by one:

| new hit | verdict |
|---|---|
| `market_data_service.get_rentcast_data` | **false positive** — called at `market_data_service.py:435` |
| `market_data_service.get_google_place_rating` | **false positive** — called at `:436` |
| `deal_dive.get_market_context` | **false positive** — called at `deal_dive.py:184` |
| `om_extract.read_pages` | **false positive** — called at `om_extract.py:212` |
| `fire_metrics_ai_summary.count_sentences` | **false positive** — four call sites in its own module |
| `openai_usage.get_usage` | genuine, but a **symmetric accessor** — its own test says it "mirrors the rentcast helper" |
| `investor_notes_match.count_mentions` | genuine — **superseded inside its own module** by `_distinct_spans`, which counts DISTINCT mentions because summing per-phrase counts was wrong |

**Five of seven are false positives: 71%.** And neither of the two
genuine hits conceals a feature, which is the thing the sweep exists to
find. `get_usage` is the same category as the existing allowlist entry
`investor_report_db.get_investor` — *"symmetric CRUD accessor… no feature
is hidden behind it"*. `count_mentions` is a leftover from a superseded
approach; it should probably be deleted, but nobody is waiting on a
screen it would have lit up.

### Why the noise is structural, not bad luck

The sweep's caller rule is **"a caller outside the defining module"**,
and that rule is exactly right for `tools/*_db.py`: a database reader
exists to be called by application code, so a reader used only within
`*_db.py` means the feature that would have surfaced it was never
written. That is what caught `list_feedback` and `list_updates`.

Outside `*_db.py` the same rule stops meaning that. A module-level helper
called only by its own module is the **normal** shape for
`market_data_service`, `om_extract` and `fire_metrics_ai_summary` — it is
what a private-but-not-underscored helper looks like. Every one of the
five false positives is that pattern.

So the widening does not have a tunable false-positive rate that better
allowlisting would fix. It asks a question whose premise only holds for
one directory.

### The answer

**Leave the glob at `tools/*_db.py`.** Widening it would add seven
entries requiring seven written allowlist reasons, five of which would
say "called inside its own module, which is fine" — and an allowlist
whose majority entry is "this is fine, actually" is how a list stops
being read. The two genuine hits are worth acting on and are recorded
here, which costs nothing and needed no sweep change.

And it would not have caught `to_capex_lines()` regardless, which was the
argument for doing it.

### What a different instrument would cost, measured rather than guessed

**What would catch this shape** is a different question, not a wider
glob: *a public module-level function called nowhere in production, not
even by its own module.* I claimed that would flag `to_capex_lines` and
none of the five false positives. Both halves check out — but the total
does not, and the first measurement was much worse than the claim
implied:

```
public module-level functions in tools/*.py        : 572
called NOWHERE in production, incl. own module     :  81
```

**81 hits.** The dominant class is one I had not accounted for: **Flask
route handlers**, which are never "called" because the framework
dispatches them by decorator. `site_dd.capex_budget`,
`underwriting.save_expenses` and fifty-two others are live, reachable
code. They are also already covered by `test_route_reachability`, which
is the right instrument for them.

Excluding decorated functions:

```
  decorated (framework-dispatched)  : 54
  UNDECORATED -- real candidates    : 27
```

27 is a defensible size for a sweep and it does contain
`to_capex_lines`, `get_usage` and `count_mentions` while containing none
of the five false positives. But 27 entries each needing a written reason
is a substantial one-off audit, and most will be legitimate public API
(`waterfall_math.amount_to_reach_irr`, `upload_limits.limit_for`,
`site_dd_checklist.is_known`) or symmetric CRUD
(`investor_report_db.delete_investor`). **That is a real piece of work
with a real payoff and it should be decided on its own, not smuggled in
as a glob change.** The numbers are here so the decision can be made
without repeating the measurement.

### One hit from that list is mine, from last run

`site_dd_db.area_status_label` — added in `5cde052`, one run ago — **has
no production caller.** The routes pass the `AREA_STATUS_LABELS` dict and
both templates subscript it directly (`area_status_labels[st]`), so the
accessor is reached only by its own test.

It is not harmful: it is four lines, and its "Not stated" fallback is the
documented behaviour for NULL and for a value left by an older
vocabulary. But it is exactly the pattern this document is about, written
one run after I described the pattern, and the sweep as it stands cannot
see it either — `site_dd_db.py` is inside the current glob, but
`area_status_label` does not start with a reader prefix.

Either the templates should call it — which is the better shape, because
the dict subscript `area_status_labels[st]` raises on an unknown key
where the function returns "Not stated" — or it should go. Not changed
this run; it is a build, and this run is investigation.
