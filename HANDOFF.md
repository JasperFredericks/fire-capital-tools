# FIRE Capital Tools — handoff

**Written 2026-08-17, updated 2026-08-18. Master at `66b2d1e`.**

This replaces an earlier handoff that had gone substantially stale. That
document's errors cost real investigation time: it had the repo under the
old owner, listed the cash-out refinance as unbuilt when the branch
already existed, and two confident premises in it were simply wrong (see
*Premises that turned out to be false*, below). **Nothing in this file
should be treated as settled unless it says it was verified, and where
something is uncertain it says so.**

---

## Where things run

| | |
|---|---|
| Repo | `firecapitaltools/fire-capital-tools` (transferred; **still public**) |
| Host | Railway, project `FIRE Capital Tools`, service `fire-capital-tools` |
| URL | `https://fire-capital-tools-production.up.railway.app` |
| Deploy | GitHub auto-deploy from `master`. Verified via the Railway GraphQL API: `serviceInstances.source.repo = firecapitaltools/fire-capital-tools` |
| Volume | `/data`, ~0.1 GB of 4.9 GB |

Every persistent database uses a `*_DB_PATH` env var pointing at `/data`.
The user sets Railway env vars themselves — do not attempt to.

Railway's GraphQL API sits behind Cloudflare and returns **error 1010**
to a request with no browser `User-Agent`. That is a blocked fingerprint,
not a bad token.

---

## What is merged and live

Twelve commits landed on `master` this session, in five merges. All were
deployed and verified against production before moving on.

| merge | what it does |
|---|---|
| `9e8fdc5` | Notetaker **Word export**, sections chosen per update |
| `4da1901` | **Dead-reader sweep** + `SOURCE_DATE_EPOCH` pin |
| `b613a76` | **Rate/count fix** in the Site DD capex export |
| `51275b9` | **Capex export links** on the assessment detail page |
| `6ba1bad` | **Route-reachability sweep** |
| `07e746e` | **Properties foundation** — a record that names its deal belongs to it |
| `e71d382` | **Manual-cost rate fix** — the unit belongs to the item |
| `aa2be2d` | **Notetaker sections v2** — CapEx Update, Next Steps, prompt v2 |

**Deployed test suite: 1450 tests, OK, 19 skipped.**

### Word export (`9e8fdc5`)

`python-docx==1.2.0` pinned. Sections are a **toggle over whatever the
update already has**, every box ticked by default — that is "all", not a
curated subset, because Michelle's real updates carried a different set
each time. `select_sections()` filters without reordering; order comes
from the update, never from the request. An empty section renders its
"not discussed" line and stays visibly empty. Nothing is generated to
fill it.

Found while checking the button was reachable: `notes_db.list_updates()`
had existed since the table did and **no route ever called it**, so a
generated update was reachable only by the redirect immediately after
making it. The index now lists updates.

### Rate/count fix (`b613a76`) — the most consequential change here

Assessment 11 exported a capital budget of **$5.75**. One line, interior
repaint. The researched figure for `walls_ceiling` is **$5.75 per square
foot**, and the export multiplied it by a quantity of 1 — where 1 was the
*instance count* the grouping produces ("forty toilets are one line of
quantity 40"). It then printed "No estimate: 0 item(s)" and "100% of the
total is researched" over the top.

**Seven of thirty-six researched figures are rates** (`RATE_UNITS` =
sqft, lf) and they are the expensive ones: flooring, repaint, roof
covering, facade, paving, roof drainage. On a full walk those dominate a
budget and every one would have come out in single digits.

Now: a rate prices only from a **measured quantity**. Without one the
line keeps its rate, states the measurement it needs, and is excluded
from the total. Per-item figures are untouched. Totals are `None`, never
`0.0` — zero claims the work is free and sums in silently.

The summary is held to the same standard: **no total at all** when lines
exist and none could be priced, "Priced subtotal" when partial, and three
buckets kept distinct — `priced` / `researched-but-unmeasured` /
`unresearched`. An **empty** budget still reports `0.00`, because nothing
recorded as needing work is a finding rather than a gap.

**What Michelle sees on assessment 11 today** (verified live, read-only):

```
Total                             | No priced lines — see below
Inspector estimate                | —
Researched average                | —
Researched rate, not yet measured | 1 item(s)
No researched figure              | 0 item(s)

Nothing here can be priced yet, so there is NO total: 1 line has a
researched rate but nothing measured to apply it to.
```

This is correct and intended. **She was emailed about it before the links
went live.** It is also the strongest argument for unblocking
measured-quantity collection.

---

## Branches

| branch | head | state |
|---|---|---|
| `uw-refi-cashout` | `13c2b7d` | **built, rebased, verified, HELD on a confirmed double-count.** See below. |
| `notetaker-word-export` | `8bde934` | merged, can be deleted |
| `dead-reader-sweep` | `df98c64` | merged, can be deleted |
| `sitedd-rate-fix` | `8a874d4` | merged, can be deleted |
| `sitedd-capex-links` | `a46bd36` | merged, can be deleted |
| `route-reachability-sweep` | `bcccecd` | merged, can be deleted |

### `uw-refi-cashout` — what it contains and what blocks it

**Rebased onto current master, fully re-verified, and HELD on a
confirmed double-count.** Do not merge it as it stands.

It implements Michelle's confirmed terms: excess refi proceeds to
investors, a 1% GP capital transaction fee, payout order payoff → fees →
return of capital, no pref at the event with pref continuing to accrue on
the smaller unreturned base. Cash-in refis are refused. Invariants 1–11
all evaluate to True with a refinance present, and the no-refi behaviour
fingerprint matches master on all five scenarios with its positive
control confirmed to fire.

**THE BLOCKER: `refi_costs_pct` ALREADY INCLUDES THE BANK'S POINT**

This is the definition that will get rediscovered expensively if it is
not written down, so it is written down.

`refi_costs_pct` was authored to mean **"points and closing"** — its
original docstring says exactly that. **A point IS lender origination**,
priced as a percentage of loan size. Four signals agree:

1. The original docstring: `- refinance costs (points and closing)`.
2. The form label: "Refinance: Costs (**% of new loan**)". Third-party
   closing costs — title, appraisal, recording — are flat dollars. A
   percentage-of-loan field is origination-shaped.
3. **The acquisition side folds them together the same way.**
   `DEFAULT_ACQUISITION_COST_CATEGORIES` lists `origination_fee` as one
   of nine line items *inside* acquisition costs, beside Legal, Appraisal,
   Lender Legal and Doc Prep. There is no separate loan-fee input anywhere
   in the app.
4. The original fixture was exactly 1.0% — $52,000 on $5.2M. Precisely
   one point, which is precisely a standard bank loan fee.

Michelle then said "there is ALSO a standard 1% loan fee that the bank
takes". She was answering about the GP fee, so "also" most naturally
means *in addition to the GP fee* — but the branch as built adds it on
top of `refi_costs` as well, charging the bank's point twice.

**Cost of the double-count**, at her real 1%/1%/1%:

| | as built | if `refi_costs` already is the bank fee |
|---|---|---|
| to investors | $663,282.61 | $715,282.61 |
| levered IRR | 18.7575% | **19.0902%** |
| equity multiple | 2.1271 | **2.1472** |

**−0.33 IRR points and −0.02 on the multiple**, on figures she acts on.

**Two resolutions, and the choice is hers:**

- **(a)** Drop `refi_bank_fee_pct`; tell her the bank's point is already
  inside "Refinance Costs".
- **(b)** Keep it, and **redefine `refi_costs_pct` as third-party closing
  costs only**, so the two are disjoint. This matches how she described
  it and gives her the visibility she asked for.

**(b) is the recommendation**, and it is free right now: production has
no refi columns at all, so there is no data to migrate. But it changes
what an existing field means, so it is not a call to make silently.

**The question is with Michelle. Do not implement either until she
answers.**

---

## Blocked on Michelle

Nothing below should be started without an answer. Each has been
investigated; none has been built.

**1. Measured-quantity collection in the Site DD UI.** *Highest value.*
Inspectors do not record areas or lengths on the walk, so every
rate-priced item — the expensive ones — is unpriceable. This is what
turns the capex export from empty into real numbers. The question that
decides the design: **do inspectors measure on the walk, and with what?**
A tape measure per room implies one UI; a single unit square-footage from
the rent roll implies a very different one.

**2. Rent-roll upload — scope is contradictory.** Her Site DD document
says *"upload rent roll to know number of units."* Her in-app feedback
asked for **bedroom derivation from unit type and occupancy mapping**.
Those are materially different asks — the first is a fraction of the
second, which is a two-to-three session build. **This discrepancy is
unresolved and is a direct question to her.** Also still blocked on a
**2BD sample file**: the only real rent roll we have (Jackson, Appfolio)
is 16 units all `1/1.00`, so the headline feature — derive two bedrooms
from the unit type — has no test case.

**3. Site DD property header** (name, vintage, address, building count,
optional sqft). Only `property_label` exists; the other four are
genuinely new — there is **no properties table anywhere** in the product,
and the 12-property registry is assembled at request time from Deal Dive,
Underwriting and Site DD labels. Three shapes were proposed
(per-assessment columns / fields on a property record / a walk-date
snapshot with visible disagreement). It is a judgment call and was left
to her. The failure mode to avoid: per-assessment columns mean retyping
everything on re-inspection and two rows silently disagreeing about the
build year.

**4. Notetaker sections.** Renaming Operations → Property Update and
Capital Improvements → CapEx Update, plus adding **Legal Update** and
**Next Steps**. **Proven not display-only**: `build_instructions()`
interpolates `s['name']` straight into the prompt, so a rename changes
1,377 characters of what is sent to the API. `cache_key()` hashes
`prompt_version`, not the prompt text, so renaming without bumping the
version would serve results generated under the old headings. One change,
and it costs real OpenAI spend against the **$60/month** budget.
Separately: the update page renders `section.name` from the **stored**
JSON, so existing updates keep old headings regardless.

**5. Refi fee base.** See above.

---

## Standing rules that survived this session

Some of these changed. Where they did, the old rule is named so it is not
reinstated by accident.

**Merge discipline.** Investigate → report → build → report before
merging → merge only on explicit go-ahead → deploy → verify on production
→ report. One merge at a time; never chain. Report each part separately.

**Verification is by behaviour, not by file hash.** *This replaced the
old rule.* `deal_analyzer_math.py` used to be checked by byte-hash — but
a branch that legitimately adds a function will always fail that, and it
proves nothing about behaviour. The current approach is a **two-signal
fingerprint matrix**: a behaviour hash over the *intersection* of keys
present on both sides, plus a schema diff reported separately and never
hashed. The one-signal version reported divergence on four scenarios in
which not a single pre-existing value had changed.

**Every comparator gets a positive control before it is trusted.** An
instrument that has never returned a difference has not been tested. This
is not optional and it has caught real defects:

- The first dead-reader sweep matched `name(` as text and was **satisfied
  by a prose comment** mentioning `list_updates()` — a checker reporting
  safety it never established. It now walks the AST.
- The first route sweep matched `url_for` only and produced **two false
  positives** (`/manifest.json`, `/service-worker.js`) in its first six
  results.

**Reachability is enforced by two sweeps, not by discipline.** *This is
new.* Four separate features shipped correct, tested and invisible:
`feedback_db.list_feedback()`, `notes_db.list_updates()`, the notetaker
itself, and the Site DD capex export. `tests/test_dead_readers.py`
requires every public reader in `tools/*_db.py` to have a caller outside
its own module and outside tests. `tests/test_route_reachability.py`
requires every GET route to be referenced by a template. Both carry
allowlists with **a written reason per entry**, and both self-check for
stale entries.

**Verify by navigating, not by driving URLs.** Every one of the four
invisible features passed its own tests. Harvest hrefs from rendered HTML
and follow them.

**Production data.** Read-only means `mode=ro`. Snapshot before any
write, and verify restoration by **content fingerprint, not file hash** —
SQLite page reuse changes the file hash after an insert-then-delete even
when the content is identical. Routes that rewrite whole collections
(`save_area`, `save_expenses`, `save_capex`, `replace_loans`) require
**complete** form posts; a partial POST silently blanks fields.
`replace_expense_lines` reassigns line IDs on every save.

**Money.** OpenAI calls spend against a shared **$60/month** budget. Make
exactly the number authorised — this was overspent once (2 calls instead
of 1). Michelle explicitly does not want scraping; reference costs are a
one-time manual research pass.

**Units differ by layer, and this produced a wrong run.**
`analyze_noi_series` takes `interest_rate_pct=6.5` (**percent**);
`monthly_payment` / `remaining_balance` / `annual_debt_service_series`
take `0.065` (**decimal**). Passing percent where decimal was wanted
produced $13,000,000 of annual debt service on a $2M loan.

---

## Paresh's inspection forms exist, and the previous handoff said they did not

**The correction.** An earlier handoff recorded that Paresh could not
provide his inspection script or form, and that **no reference
implementation had ever existed**. That is false. On 2026-08-18 he sent
four mature production instruments, in real use before our rebuild:

| file | what it is |
|---|---|
| `The_View_Inspection_XLSForm_v7.xlsx` | KoboToolbox unit inspection, 344 survey rows, 35 choice lists |
| `The_View_Building_Exterior_Inspection_v5.xlsx` | exterior inspection, 672 survey rows |
| `rent_roll.csv` | 84 units, bed/bath **pre-derived** into separate columns |
| `property_config.csv` | 3 rows of per-property settings driving question relevance |

**That entry was load-bearing: it is why Site DD was rebuilt from
scratch.** The rebuild happened without them, and the rebuilt tool is not
wrong -- its repeatable-items design is structurally better than the
exterior form's 672 hardcoded rows, where a four-floor building needs a
whole new form. But the checklist *content* in those files is mature in
ways ours is not, and none of it informed the rebuild.

Version numbers are the tell we missed: **v7 and v5**. Those are not
drafts. Somebody iterated on them in production for a long time.

Files are in the user's Downloads folder as of 2026-08-18. **Get them
into durable storage** -- a Downloads folder is not where the only copy
of a reference instrument should live.

---

## THE STANDING RULE THIS SESSION EARNED

**Check the premise against the code before scoping anything.**

Three separate things were treated as blockers, in some cases for
several rounds, and each cost nothing once somebody actually looked:

| assumed blocker | reality |
|---|---|
| Notetaker section changes cost real OpenAI spend | Production had **zero** transcripts and zero updates. Nothing cached, nothing to regenerate. The bump was free. |
| `underwriting_scenarios` needs a `deal_id` migration | It **already had one**, in the base schema, with an index, NULL on all ten rows. |
| A Site DD rent-roll upload needs a new parser | The existing ResMan parser already returns all 152 Oxford Pointe units correctly. It only cannot **open** `.xls`. |
| Paresh could not provide his inspection form; no reference implementation ever existed | He sent four, on being asked. v7 and v5, in production use. **Nobody re-asked for eight months.** |

The first three needed one query or one grep apiece. The fourth needed an
email. The pattern is that a plausible-sounding claim hardens into a fact
the moment it is written down, and nobody re-checks it because it already
sounds settled.

**THE SHARPENED FORM: an unavailable resource is worth re-asking for, not
just re-checking in the code.**

The first three were premises about our own code, and a grep settles
those. The fourth was a premise about a **person** -- what somebody could
or would provide -- and no amount of reading the codebase could ever have
falsified it. It stayed true-sounding for eight months and it caused a
from-scratch rebuild.

Availability is a fact about a moment, not a property of a resource.
People find files, change jobs, change their minds, or were asked
badly the first time. When a "cannot be obtained" is load-bearing --
when it is the reason something is being built the hard way -- re-ask
before committing to the expensive path.

Two false premises earlier in the same session -- see below -- came from
exactly the same mechanism, and they cost investigation time rather than
just delay.

So: before scoping, estimating, or declining anything, spend the one
minute it takes to check it against the code. Especially when the claim
came from a previous handoff, and especially when it is the reason
something is not being done.

---

## Premises that turned out to be false

These came from confident statements in a previous handoff, in briefing
text, or -- in the fourth case -- from one of our own reports. Every one
cost investigation time. **Check premises against the code**, and where
the premise is about a checker we wrote, run the checker.

1. *"Quick Deal Analyzer shares `deal_analyzer_math`."* It does not.
   `quick_analyzer_math.py` imports only stdlib; every mention of
   `deal_analyzer_math` in it is docstring prose, including the line
   "deal_analyzer_math.py is not imported here." The only live caller of
   `analyze_noi_series()` is `underwriting_math.py`.

2. *"The bedroom filter refetches and recency filters client-side, which
   is why one works and the other doesn't."*  Both are client-side and
   wired identically -- `bedsSel.addEventListener("change", apply)` and
   the same line for recency. The differential a tester observed was
   **comp-set composition**: RentCast returns comparables matched to the
   subject's size, so when it resolves the subject as a studio, all
   fifteen comps are studios and "studio" versus "all sizes" show the
   same rows. Plausible, survived the reporter's own description of the
   symptom, and died on the cached data.

3. *"Site DD has PDF export only; no XLSX path exists anywhere."*
   `site_dd_capex_export.build_xlsx()` had been live the whole time,
   wired to `/tools/site-dd/assessment/<id>/capex.<fmt>`, already
   satisfying every requirement that was being specified as if new. The
   real defect was that nothing linked to it.

4. *"Widening the dead-reader sweep's `tools/*_db.py` glob would have
   caught `to_capex_lines()`."* **It would not.** The sweep is gated on
   **two** things and only one of them is the glob:

   ```
   READER_PREFIXES = ('list_', 'get_', 'fetch_', 'find_', 'count_',
                      'search_', 'load_', 'read_')
   ```

   `to_capex_lines` begins with `to_`, so it fails the prefix gate
   wherever it lives. Catching it needs the prefix list widened too,
   which is a different and much noisier instrument -- measured in
   `docs/site-dd-to-capex-lines.md` at 81 hits, 54 of them
   framework-dispatched routes.

   **This one is worth more than the other three, because of how it
   propagated.** It was asserted in a Part 31 report, repeated back in
   the Part 35 prompt as settled, and acted on as the premise for a
   piece of work. Neither side checked the mechanism. A claim that
   travels from a report into a prompt and back has been *confirmed by
   repetition*, which is not confirmation at all -- and it is the same
   failure as the RentCast disclosure, where four cached addresses
   agreeing stood in for checking all seven.

   The rule that catches this one is narrower than "check premises
   against the code", because the premise here was about **our own
   instrument**: *before claiming a checker would or would not have
   caught something, run it.* It takes one command.

5. *"`area_status_labels[st]` raises on an unknown key."* **It does not.**
   This app runs Jinja's **default `Undefined`**, so a missing key
   renders as the **empty string**:

   ```
   status=occupied           subscript=' &middot; Occupied'
   status=vacant_not_ready   subscript=' &middot; '          <- dangling
   status=None               subscript=''
   ```

   The accessor was still the right change, for a worse reason than the
   one given: silent, not loud. An unrecognised value produces a dangling
   separator and leaves no trace that anything was wrong, where
   `area_status_label()` answers "Not stated". Note the NULL case, which
   was the one flagged as the live hazard, is **not** the hazard at all --
   both display sites are guarded by `{% if area.status %}` and render
   nothing either way. The hazard is a non-NULL value from an older
   vocabulary, which is exactly what widening AREA_STATUSES would
   introduce.

   Both behaviours are now pinned in
   `tests/test_area_status_labels.py`, so configuring Jinja with
   `StrictUndefined` fails a test rather than silently invalidating a
   docstring.

   **Same propagation path as #4, one run later.** Asserted in a Part 35
   report, restated in the Part 36 prompt as the reason to do the work,
   acted on by both sides. Nobody rendered a template.

**Four of these five share one shape: a claim about a MECHANISM -- a
parser, a checker, a template engine -- believed rather than executed.**
The rule from #4 covers all of them and is worth reading as general:
*before claiming what a mechanism does, run it.* Every one of these cost
more to discover than the single command that would have settled it.

A claim that travels from a report into a prompt and back has been
**confirmed by repetition**, which is not confirmation. When a prompt
states one of our own findings back as settled, that is the moment it is
least likely to be re-checked and most likely to be wrong.

### 6. An APPROVAL can outlive its premise, and nothing expires it

*"Normalize at entry — canonicalise the address when a deal is created or
edited."* Approved in Part 23, restated in prompts, **unbuilt for fifteen
runs**, and investigated properly for the first time in Part 38. It is
aimed at a code path the problem does not use.

The duplicate rows came from the **Rent Comps standalone search box**,
which takes a free-text address whenever no `deal_id` is supplied.
Production has two deals; neither is Steiner or Belvedere, and **ten of
twelve cached rows correspond to no deal at all**. A canonicaliser on
`new_deal()` / `edit_deal()` would have run **zero times** against the
addresses that actually collided — and could not have merged them anyway,
since they differ by the street-type suffix that the same decision
correctly ruled out.

**This is a different failure from the five above.** Those are false
*claims*, and the fix is to run the mechanism. This is a false *plan*: the
reasoning that produced it was sound, the constraint it protects is still
right, and the decision was never wrong when made — it was aimed using a
premise about where the data comes from that nobody had checked.

An approval is the most durable thing in a handoff. It reads as settled,
it carries the authority of having been decided, and unlike a claim it
invites no re-examination at all — the question feels closed. It sat
through fifteen runs precisely *because* it was approved.

**The rule: when an approved-but-unbuilt item finally comes up to build,
re-derive what it is aimed at before building it.** Not whether it is a
good idea — that was settled — but whether the thing it targets is the
thing that is broken. The check here was one query against the deals
table, and it retired most of a plan that had been carried for fifteen
runs.

---

## The falsy-zero audit: one member, and the convention it implies

`{{ rentcast.property.bedrooms or '—' }}` rendered a **studio as unknown**,
because 0 is falsy. That looked like a class of bug, so all 53 `or`-fallback
idioms in templates plus 6 in Python were audited.

**The class has exactly one member.** Everything else is safe, and for
reasons worth recording so nobody re-runs the audit:

  * `gp.rating`, `review.rating` -- Google ratings are 1.0-5.0; absent is None
  * `crime_rating`, `climate_risk_rating` -- these are LABELS; the numerics
    are separate fields (`crime_index_score`, `climate_risk_score`)
  * `hold_years` -- `_validate()` rejects `< 1`
  * OM `property[k]` / `asking_terms[k]` -- the schema declares **every
    field `{"type": "string"}"`, deliberately, because they are verbatim
    quotes. Empty string is the real "not stated" case.
  * everything else is a date, name, note, caption, label or address

`bedrooms` was the only member because it is the one field here where
**zero is a common, real, user-facing value** -- a studio.

**THE HOUSE CONVENTION: guard the container, not the number.**

    {{ money(x) if obj else '—' }}     right -- asks "does the object exist"
    {{ x or '—' }}                     wrong for anything numeric

Every ternary in the codebase already guards the container, which is why a
genuine zero NOI still renders as `0`. Keep it that way.


---

## Four things about specific bugs, kept because each will recur

### "Fails on one property" has been wrong four times. Check all properties first.

**Standing rule: before accepting a property-specific premise, test the
other properties.** Four instances now, all in one testing cycle:

  1. *Bedroom filter doesn't refresh* -- comp-set composition, any address
     whose comps are uniform behaves the same.
  2. *MMR prints 33 pages, "all but oxford work"* -- the stray selected tab
     differed per SOURCE FILE, not per property. ERA was clean by luck of
     how its workbook was saved. Fixing "the Oxford bug" would have fixed
     nothing.
  3. *Scorecard trend chart unlabelled for Jackson* -- any upload whose GPR
     parses as zero behaves identically.
  4. The GPR parsing bug below, same shape.

The framing is seductive because it sounds like a narrowed, tractable
problem. It usually means "this is the one the tester happened to open".

### The MMR print mechanism, because a new source workbook will hit it again

The download is the source MMR with a Summary sheet prepended, and it
inherits **whatever tabs were selected when somebody last saved the source
file**. Excel's default print option is "Print Active Sheets" -- PLURAL --
so a stray selected tab prints alongside Summary, and source sheets are
raw exports with no print area (General Ledger is 1,168 rows on OXPT).

`scope_workbook_for_print()` deselects everything but Summary and gives
each content-bearing source sheet a print area. Measured before the fix:
OXPT carried "Prospect Source Summary", Maple Valley "Cash Flow", Canyon
"Work Order Summary", ERA nothing. **The selected-tab state varies per
uploaded file**, so a new source workbook can present as a new bug.

### The address-duplicate decision: do NOT change normalize_address_key

> **Part 38 Step B investigated this and the plan below does not survive
> the data.** The four duplicate rows came from the **Rent Comps
> standalone search box**, not from deal entry — production has two deals,
> neither of them Steiner or Belvedere, and ten of twelve cached rows
> match no deal. So "canonicalise when a deal is created or edited" would
> have run zero times against the addresses that collided, and could not
> have merged them anyway, since they differ by the street-type suffix
> that is ruled out just below. Both pairs were confirmed to hold
> equivalent data; nothing was deleted. Full write-up, including a live
> ZIP+4 cache-miss defect on deal 1, in
> `docs/address-normalize-at-entry.md`.

The cache holds separate paid rows for `24 steiner` / `24 steiner street`
and `598 belvedere` / `598 belvedere street`, because the key function only
lowercases and collapses whitespace.

**Merging them requires dropping the street-type suffix, and that collides
real addresses**: `100 Main St`, `100 Main Ave` and `100 Main Blvd` all
become `100 main`. Those coexist in real cities, and serving one street's
comps for another is far worse than four wasted calls.

Changing the key function also **orphans every existing cached row** --
they carry keys under the old function, so every lookup misses and triggers
a fresh paid call. Re-warming 12 rows to prevent 4 costs more than it
saves, immediately.

**Decision: normalize at ENTRY instead** -- canonicalise the address when a
deal is created or edited, one address at a time with a human present.
Approved, not yet built. Plus a one-off merge of the four duplicate rows.

### Jackson's GPR does not parse, and the blast radius is not the chart

`kpis.py:271` reads Gross Potential Rent from **one hardcoded account
code**:

    gpr = self.get_val("4110", month)

The parser already carries multiple chart-of-accounts dialects --
`parsing.py` maps `"Gross potential rent"`, `"Gross Potential Rent"`,
`"Gross Potential Rent (Scheduled)"`, a Paresh code map (`40210 -> 4110`),
and `detected_format` values including `Canyon`, `Paresh` and `Cash Flow
(Generic)`. **A GPR code that varies by source is a known shape here, not
a surprise.** So the likely cause is that Jackson's dialect is unmapped,
not that its data is missing.

**When GPR parses as zero, the consequence is not cosmetic:**

  * `phys_occ` and `econ_occ` are set to `None` -- that property shows **no
    physical occupancy and no economic occupancy**
  * NRI reconstruction is skipped -- `if "4000" not in self.accounts and
    gpr != 0` -- so if code 4000 is also absent, **NRI is not rebuilt either**
  * it previously blanked every dollar label on the trend chart (fixed)

**Any upload whose GPR parses as zero behaves identically.** Jackson is the
one that was tried.

**Not fixed. It needs Jackson's actual upload** to see which account codes
and `detected_format` it produces. That single fact picks the fix.

**On the honest-incompleteness question: the state already exists, and it
is confidently wrong about the cause.** `scorecard_pro.html` already
renders a warning listing the affected months:

> "Occupancy could not be worked out for N months. **This file does not
> state Gross Potential Rent** ... Those months are left out of the average
> occupancy figure; **every other number is unaffected**."

Two problems with that wording, both worth fixing when the dialect fix is
written. It asserts *the file does not state GPR*, which the code cannot
know -- what it knows is that **no line matched code 4110**. And "every
other number is unaffected" is false: NRI reconstruction is skipped, and
until recently the chart labels were affected too. The pattern is right;
the claim is stronger than the evidence.


---

## A claim that holds across the sample you have is not established

Three instances this session, all the same shape: someone reasons to a
plausible mechanism, checks it against the cases that prompted the
question, finds agreement, and ships it as fact.

  1. **The refetch-versus-filter hypothesis.** Both rent-comp selects are
     client-side and wired identically. The hypothesis survived the
     reporter's own description of the symptom and died on the cached data.
  2. **Scorecard Pro's warnings card** asserts *"this file does not state
     Gross Potential Rent"*. What the code knows is that **nothing matched
     account code 4110** -- and the parser carries several account
     dialects, so those are different claims. It also promises "every
     other number is unaffected" while NRI reconstruction is skipped.
  3. **The RentCast subject disclosure**, which we wrote ourselves *one
     step after* flagging (2). It asserted comparables are matched to the
     subject's size, inferred from four cached addresses where it held.
     False on three of the seven we had: Lubbock 2 of 15, both Belvedere
     rows 3 of 15.

**The rule: check the claim against every case you have, not the ones
that prompted it.** Seven addresses were sitting in the cache the whole
time; looking at four of them is what shipped a false statement to a real
user.

### A proxy that looks decisive: comp-set identity is NOT same-address

Part 38, deciding whether two cached rows were genuine duplicates. The
obvious test is whether they carry the same comparables. It is worthless:

```
22 Steiner St  VS  24 Steiner   ->  same comp set: True, overlap 15 of 15
                                    same rent estimate, same range
                                    same distances to FOUR decimals
```

**Two different buildings, indistinguishable in the cache.** RentCast
resolved no subject property for either, so both got the same area-level
sample — and where it resolves no subject, comparables are not matched to
the subject at all. Every address on that block gets the same fifteen.

So an identical comp set is evidence of *proximity*, not identity, and it
is exactly the kind of proxy worth distrusting: high-dimensional
(15 addresses, prices, distances), expensive-looking, and intuitively
conclusive — fifteen identical records *feels* like proof. The real
discriminators are the **subject resolution** and the **rent estimate**.

**It was caught only by a positive control, and the control fired on the
second try.** The first comparator keyed comparables on `id`; there is no
`id` field in a cached comparable, so every `.get("id")` returned `None`
and the check compared `[None] * 15` against itself. It would have called
two unrelated comp sets identical. Re-keyed on `address` it ran correctly
— and then the control against a known-different address *failed*, which
is what exposed the proxy.

Two lessons, and the second is the one that generalises: **a comparator
keyed on a field that does not exist reports agreement, not an error** —
so assert the key is present before trusting the comparison. And a
positive control is not only a test of the instrument; **when it fails it
is sometimes telling you the signal itself is not discriminative.**

The corollary for messages specifically: **a page may only state what it
can count.** If the composition is in the DOM or the payload, compute the
sentence from it. If the cause is genuinely unknown, say what is missing
rather than why.

### The audit that found it

Five honest-incompleteness messages were checked against what the code
establishes. Four are sound: the capex three-bucket sentence (it counts
`is_rate and unit_cost is not None` versus `unit_cost is None`), "cost
entered, unit not specified" (cost exists, unit is None, and the
magnitude hint is explicitly labelled a hint), the unpriced-item reasons
(human-authored claims about the world that do not pretend to be derived),
and the capex "whole **recorded** budget" phrasing, which is carefully
qualified.

The fifth was the RentCast disclosure, now fixed.

One message is sound but **prescribes an action nobody will take**:
"Needs a measured floor area before it can be totalled." Michelle's answer
-- *"don't worry about calculating paint, we just need to determine the
conditions"* -- means no measurement will ever be recorded. The
"condition-only, priced by scope" rewording is queued and now has two
reasons.

---

## Uncommitted work reported as a branch, twice

**Before reporting a branch as ready, verify it exists at the claimed SHA:
`git log -1 <branch>` plus `git status --porcelain` for a clean tree.**

Twice this session a report said "built and verified on branch X" when the
work was sitting **uncommitted in the working tree** and the branch pointed
at master's head:

  * `sitedd-checklist-gaps` (Part 21) -- caught at the start of the next
    run, which opened by committing it.
  * `scorecard-trend-labels` (Part 26) -- caught only when `git merge`
    answered "Already up to date."

Nothing was lost either time, but the reports claimed a durability they
did not have, and a context-limited session ending on that claim is
exactly how work does get lost. Tests passing is not evidence the work is
committed.


---

## Things that are true and easy to lose

**`SOURCE_SITE_DD` is dead code, and it is the model for how to leave
one.** `underwriting_capex.py` defines it and `summarize()` counts it,
and **nothing writes it**. Its own comment says it is "the reserved value
for rows Site DD's repair list will one day write" -- and that sentence is
the entire reason its status was recoverable. Production holds 4 capex
lines, all `source='manual'`. Consequence, verified: **Site DD capex does
not reach Underwriting**, so the rate bug never touched equity, IRR or
equity multiple. It wants an implementation, not a cleanup: see the
waiting-half convention below.

---

## Dead paths: five found, four different ways, and the convention that follows

**Five features have shipped correct, tested and unreachable:**

| | found by |
|---|---|
| `feedback_db.list_feedback()` | the dead-reader sweep |
| `notes_db.list_updates()` | the dead-reader sweep |
| the notetaker itself | a navigation check -- the sweep cannot see a self-referential cluster |
| the Site DD capex exports | looking for links from the detail page |
| `site_dd_costs.to_capex_lines()` | an investigation, Part 35 |

**Four different means. A sixth instrument is not the lesson.**

That was measured rather than assumed. The candidate sweep -- *a public
module-level function called nowhere in production, not even by its own
module* -- yields **81 hits**, of which **54 are Flask route handlers**
the framework dispatches and `test_route_reachability` already covers,
leaving **27** real candidates. Triaged:

| | |
|---|---|
| legitimate public API, uncalled today | 11 |
| symmetric CRUD accessors | 4 |
| internal helpers of one large module | 7 |
| genuinely superseded leftovers | 3 -- **deleted** |
| real unfinished work | 1 -- `to_capex_lines` |

**None of the 27 concealed a feature.** That is the number that decides
it. `list_feedback` and `list_updates` were different in kind: each was a
working feature with no way in, so a person lost something every day.
Nothing in the 27 is costing anyone anything. A sweep producing 27
entries needing 27 written reasons, 26 of which say "this is fine", is
permanent maintenance for a yield already in hand.

### The waiting-half convention

**When a feature is built in halves and one half ships alone, that half
carries a comment saying what it is waiting for.** A convention, not an
instrument, and it costs nothing.

The comment states three things:

1. **That nothing calls it**, so a reader is not left inferring it.
2. **What the other half is** -- the route, the writer, the screen.
3. **Whether it is safe to wire as-is.** This is the part `SOURCE_SITE_DD`
   did not need and `to_capex_lines` badly did: it has drifted three
   correctness fixes behind `build_lines()`, and connecting it would put
   the `b613a76` rate bug into equity and IRR. Someone finding it in six
   months needs that warning **in the file**, not only in a doc.

Both live examples are worth reading: `underwriting_capex.SOURCE_SITE_DD`
for the short form, `site_dd_costs.to_capex_lines()` for the form that
also has to say "do not connect this yet".

### The three that were deleted, and why they were not waiting halves

`investor_notes_match.count_mentions`, `openai_usage.get_usage`,
`site_dd_reference_costs.coverage`. The test that separates a leftover
from a waiting half: **does anything else now do the job?**

* `count_mentions` -- superseded *inside its own module* by
  `_distinct_spans`, which counts DISTINCT mentions because summing
  per-phrase counts was wrong. Its word-boundary test was **kept** and
  rerouted through `score()`; the property is real and this app has a
  deal called Jackson, so "jacksonville" must not match it.
* `get_usage` -- a single-feature slice of what the live
  `usage_for_month()` already returns in full.
* `coverage` -- its docstring said "for the report header", which reads
  like a waiting half. It is not: that header exists and was built
  differently, from budget *lines* rather than catalogue keys, as
  `summarize()`'s three buckets plus `researched_pct`, with the reference
  table and the unpriced list printed in full on their own sheets.

`to_capex_lines` fails that test and so survives: nothing else writes
Site DD findings into Underwriting.

---

## The ten label-map subscripts, and where each guard actually sits

Recorded for whoever widens a vocabulary next. `AREA_STATUS_LABELS` and
`ASSESSMENT_STATUS_LABELS` are now read through accessors and a test
forbids subscripting them. **Ten other subscripts remain, all safe today,
but not all safe for the same reason** -- and the difference is what
matters when a vocabulary grows.

Jinja here runs the **default `Undefined`**, so a missing key renders as
the **empty string**. A subscript that misses is therefore silent, not
loud (see false premise #5).

| site | expression | guarded by |
|---|---|---|
| `site_dd_area.html:19` | `condition_labels[c]` | **construction** -- `c` iterates `CONDITIONS` |
| `site_dd_area.html:76` | `room_type_labels[r.room_type]` | **a route check** -- `site_dd.py:622` |
| `site_dd_detail.html:242` | `condition_labels[c]` | **construction** |
| `site_dd_room.html:3` | `room_type_labels[room.room_type]` | **a route check** |
| `site_dd_room.html:24` | `condition_labels[c]` | **construction** |
| `site_dd_room.html:48` | `room_type_labels[room.room_type]` | **a route check** |
| `site_dd_room.html:310` | `room_type_labels[prev_room.room_type]` | **a route check** |
| `site_dd_room.html:346` | `room_type_labels[r.room_type]` | **a route check** |
| `investor_notes.html:105` | `source_labels[s]` | **construction** -- `s` iterates `sources` |
| `underwriting_detail.html:392` | `capex_scope_labels[s]` | **construction** -- `s` iterates `capex_scopes` |

**Five are guarded by construction** and cannot break: the loop supplies
the key. **Five are guarded by a route check** --
`if room_type not in uc.ROOM_TYPE_LABELS` at `site_dd.py:622`, before
`create_room()` is reached. `create_room()` itself does not validate.

That is the fragile half. The guard sits one layer away from the read, so
a second writer -- a bulk import, a copy-layout variant, a fixture, a
future rent-roll seeder creating rooms directly -- writes an unvalidated
`room_type` and five templates go quietly blank. Nothing fails; a room
simply loses its name.

**This is the same reasoning that made the status maps worth doing before
the widening rather than after.** The cheap version of the protection is
this table; the real version is an accessor at each read. If room types
are ever widened, do the accessor first.

**The Railway token in `~/.railway/config.json` expires roughly hourly,
and a stale one looks exactly like a permissions problem.** Every
GraphQL query returns `Not Authorized` -- not `401`, not `expired`, just
Not Authorized on every field including `me`. This cost real time: it
presents as "our token lacks the scope for this", and the natural next
move is to go hunting for a permissions fix that does not exist.
`railway status` refreshes it. Check expiry before concluding anything
about access:

    python -c "import json,pathlib,datetime as d; u=json.loads((pathlib.Path.home()/'.railway'/'config.json').read_text())['user']; print(d.datetime.fromtimestamp(int(u['tokenExpiresAt'])))"

**Push safety is `git merge-base --is-ancestor origin/master master`, not
`local == origin`.** After committing, local is *supposed* to be ahead of
origin, so an equality check fails every time and reads as "master moved,
do not push". The ancestor check asks the real question: has origin moved
somewhere my commit is not built on? This is not academic -- **Beckett
pushes to master directly**, and did so three times inside a single run
on 2026-08-18. Fetch and re-check before every merge; do not trust a
local head.


**The acquisition and refinance sides now disagree about origination, on
purpose.** `refi_costs_pct` means third-party closing costs ONLY -- title,
appraisal, legal, recording -- because Michelle chose to split the
lender's point into its own visible line
(`refi_bank_fee_pct`). `DEFAULT_ACQUISITION_COST_CATEGORIES` still folds
`origination_fee` in as one of nine line items inside acquisition costs.

So the same word means different things in two tools. That is recorded in
`deal_analyzer_math.refinance()`'s docstring and pinned by a test, and it
is deliberate: **Michelle was asked about the refinance side and was not
asked about the acquisition side**, so changing acquisition would have
been inventing an answer.

Someone will find this and think it is a bug. It is not. It is an
unasked question. The fix, if she wants one, is to split acquisition the
same way -- but that is her call and it touches a tool she did not raise.


**The route sweep cannot see a self-referential cluster.** Pages that
link only to each other all look referenced while the group has no way
in — which is exactly what the notetaker was. Confirmed by running the
sweep at the commit before the nav entry landed: **it does not flag it.**
`NavShellTests` is the narrow answer (every blueprint index must appear
in `base.html`) and is labelled partial in the file. It does not
generalise to deep pages.

**Eagle Rock's confirmed figures**, reproduced exactly from production
scenario 4 by passing `capex_lines=` to `analyze_scenario()`:

| | |
|---|---|
| NOI year 1 | $482,120.76 |
| equity invested | $2,688,848.65 |
| levered IRR | 19.11% |
| DSCR | 1.3990 |
| equity multiple | 2.2645 |

The four capex lines sum to $97,665.38; with 5% contingency, $102,548.65
— exactly the equity difference from the no-capex run, which produces
20.12% and 2.3543. Both figures are real; they differ only by capex.

**`test_fire_metrics_improvements` accounts for a 161-test gap** between
local and container runs. It imports `httpx`, which is **undeclared** —
it arrives only as a hard dependency of `openai`. Locally, where `openai`
is absent, the whole module fails to import. Beckett's code; not fixed
here. `openai` already ships an `httpx2` extra, so the fragility is real
rather than hypothetical. Reconcile counts by **unique test ID**, never
by line-grep — a line-grep tally produced a phantom 14-test discrepancy.

**Assessment 11's fingerprint: `11fdd001f2fca08e` is RETIRED.** Its
algorithm was never written down and it cannot be reproduced, so it could
never have detected a change — an unreproducible fingerprint is not a
check, it is a number that gets copied forward. Two replacements, each
with its algorithm stated, because they answer different questions:

**The DATA fingerprint — has the assessment itself changed?**

```
sqlite3, mode=ro, /data/site_dd.db
rows = SELECT * FROM site_dd_findings WHERE assessment_id=11 ORDER BY id
blob = json.dumps([dict(r) for r in rows], sort_keys=True, default=str)
fp   = hashlib.sha256(blob.encode()).hexdigest()[:16]

2026-08-20  ->  f6451ecb366f6ab4   (23 findings)
```

**The EXPORT CONTENT hash — has what Michelle READS changed?**

Hashes rendered content, never file bytes: a PDF carries timestamps and a
XLSX carries zip metadata, and neither says anything about the budget. It
replicates the export route exactly — `needs_work()` filter,
`apply_reference()`, `build_lines()`, `summarize()`, `build_xlsx()` — then
hashes the summary, each line's visible fields, and every XLSX cell.

```
2026-08-20  ce25f0d9ad5de0e8  -> d0b8436a3998f63b   (Part 39 Step B)
```

The two are independent on purpose. The data hash held steady across that
change while the export hash moved, which is the correct signature for a
wording change: her walk did not change, what the budget says about it
did.

**Assessment 11 is Michelle's live work.** Nabob Hill, inspector MJ,
2026-08-16, one unit, one kitchen, 23 findings. Read-only, always. Its
`property_label` created a 12th entry in the notetaker property registry
and does not resolve to Deal Dive deal 2 (1120 Jackson Street) *by label*,
which is plausibly the same building.

**CORRECTED 2026-08-20.** The sentence that stood here — "`deal_id` is
`None` on all three assessments and nothing populates it" — was wrong on
both counts, and read read-only to check:

```
assessment  2 | '19 bay vista drive' | deal_id = None
assessment  6 | '19 bay vista drive' | deal_id = None
assessment 11 | 'Nabob Hill'         | deal_id = 2      <- set
assessment 12 | 'Nabob Hill'         | deal_id = None
```

There are **four** assessments, not three, and **assessment 11 is already
linked to deal 2**. `create_assessment()` takes `deal_id` in its INSERT
(`site_dd_db.py:537`), so it is populated at creation — "nothing populates
it" was a claim about our own code that one grep settles, which is
false-premise shape #4 exactly. Whether Michelle has confirmed the two are
the same property is a separate question and is still open; what is
settled is that the column is written and this one is set.

**The propagation on this one is worse than the usual shape, and it is
worth being blunt about it.** The link was not discovered — *it was
ordered*. The user asked for assessment 11 to be linked to deal 2, it was
done, and the false sentence went on standing in HANDOFF and was repeated
across several prompts afterwards **by the person who had ordered the
link**. So this is not "a plausible claim nobody re-checked". It is a
claim contradicted by an action the author took themselves, which the
written record outlived.

The other false premises argue for *checking the code*. This one argues
for something the code cannot supply: **when you have changed the world,
go back and correct what the document says about it.** A handoff is not
just a place claims go stale — it is a place your own completed work can
be overwritten by an older sentence.

---

## Closed, unconfirmed

**Deal Dive search box.** Michelle reported a search problem; asked later
which screen she meant, she replied *"I CAN'T REMEMBER…"*. The fix that
went in — `ae19794`, "make the filter box say what it is, and wire up
Enter" — is live on master and is correct on its own terms: the box now
labels itself as a filter and Enter submits.

**Closed without confirmation that it was the screen she meant.** Nobody
has matched the fix to the original report, and nobody now can. If a
search complaint resurfaces, treat it as a new report rather than a
regression of this one. Do not spend further time reconciling it.

---

## Open operational items

- **The repo is public.** `private: false`, 0 forks/stars/watchers.
- **`uw-refi-cashout` is held on Michelle's answer**, not on staleness.
  It was rebased and re-verified; the blocker is the fee-base
  double-count described above.
- **Five merged branches can be deleted** once you are comfortable.
- **A cosmetic warning** on every Site DD PDF report:
  `site_dd_report.py:146 UserWarning: No artists with labels found to put
  in legend`. Harmless, noisy, unfixed.
- **`GET /` , `/manifest.json`, `/service-worker.js`** are reachable via
  literal paths rather than `url_for`; the route sweep understands this.
  Three routes are allowlisted: `fire_metrics.debug_refresh` and the two
  POST-minted token downloads.
- **Do not start the Entrata parser seam.**


---

## The $15 freeform rate ceiling is provisional

`site_dd_reference_costs.FREEFORM_RATE_CEILING = 15.00` decides whether a
hand-typed cost on a **freeform** item (one with no reference entry, so no
unit to inherit) is read as a rate and refused a total, or as a job price
and multiplied by the instance count.

**The reasoning:** every researched rate in the table is at most $11.50;
the cheapest researched per-item figure is $195. Nothing occupies that
seventeenfold gap, so $15 sits just above the rate ceiling and refuses as
little as possible.

**Why it is a guess, not a derived constant.** That gap is evidence about
the **36 curated entries**. A freeform item is by definition not one of
them, so the number is applied to exactly the category it was not
measured on.

**Known failure mode, asserted in a test:** a freeform "replace one
outlet cover, $8" is refused. That is a real line item and the
justification — no capital job costs twelve dollars — is an assumption
about a field built to hold anything.

The behaviour is still correct: silently multiplying a rate by a headcount
is the worse error, and a refusal is visible and correctable where a wrong
total is neither. **The real fix removes the guess**: an explicit
per-item/per-unit choice on freeform costs. That is a UI control Michelle
has not asked for, and it is flagged rather than built.

---

## Revised cost estimates (2026-08-17)

Several of these moved once the premise was checked. See the standing rule
above.

| item | cost today |
|---|---|
| **Site DD rent-roll upload** | **Roughly halved.** The original 2–3 session estimate assumed a new parser. The existing ResMan parser already returns all 152 Oxford Pointe units correctly — it needs a **loader branch plus `xlrd`**. Remaining: ~1 session for the parser/Underwriting path, a second for Site DD seeding. The idempotent re-upload reconcile is the expensive part, not the parsing. |
| **Site DD property header** | **Now small.** The `deals` columns landed in `07e746e`. What remains is a form block and a display block. |
| **Site DD Lite** | **Small.** `status` exists, is validated, and is displayed in three templates. It is a query filter plus a UI control. It never shipped because nothing consumed the field, not because it was hard. |
| **Entrata parser seam** | **Deliberately unscoped.** We have never seen an Entrata file, so every estimate would be fabricated. Do not scope it until a sample exists — the Oxford Pointe experience is the argument: the file format decided the answer, not the design. |
| **`SOURCE_SITE_DD` cleanup** | Trivial: delete a constant and a counter branch, or implement the hand-off. |
| **Manual freeform UI control** | Small, but unrequested. See the provisional threshold above. |

---

## What has not been verified

Stated plainly so it is not mistaken for tested ground.

- ~~The AI synthesis path was never exercised.~~ **Now verified.** One
  authorized generation on 2026-08-17 against a deliberately thin
  transcript returned all six headings correctly named, and **Next Steps
  came back empty** rather than inventing a plan — which is the specific
  failure that section invites. Tagged `investor_notetaker`, counter 1 → 2.
  Transcript deleted, notes fingerprint restored exactly.
- **No 2BD rent roll has ever been parsed.** Bedroom derivation is
  designed and unbuilt, and the design rests on a single Appfolio file
  whose units are all `1/1.00`.
- **The unit-label normalizer is a spec, not code.** The 60% threshold
  for detecting a letter-labelled building is a guess from one file and
  should be revisited the moment a second lettered roll exists.
- ~~Manual costs could reproduce the rate bug.~~ **Closed in `e71d382`.**
  The unit is looked up from the item, so a hand-typed figure on
  walls_ceiling is a rate. Freeform items are covered by the provisional
  $15 ceiling described above — which is itself the remaining soft spot.
