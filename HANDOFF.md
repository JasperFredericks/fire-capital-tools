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

Both came from confident statements in the previous handoff or in
briefing text, and both cost investigation time. **Check premises against
the code.**

1. *"Quick Deal Analyzer shares `deal_analyzer_math`."* It does not.
   `quick_analyzer_math.py` imports only stdlib; every mention of
   `deal_analyzer_math` in it is docstring prose, including the line
   "deal_analyzer_math.py is not imported here." The only live caller of
   `analyze_noi_series()` is `underwriting_math.py`.

2. *"Site DD has PDF export only; no XLSX path exists anywhere."*
   `site_dd_capex_export.build_xlsx()` had been live the whole time,
   wired to `/tools/site-dd/assessment/<id>/capex.<fmt>`, already
   satisfying every requirement that was being specified as if new. The
   real defect was that nothing linked to it.

---

## Things that are true and easy to lose

**`SOURCE_SITE_DD` is dead code.** `underwriting_capex.py` defines it and
`summarize()` counts it, and **nothing writes it**. Its own comment says
it is "the reserved value for rows Site DD's repair list will one day
write". Production holds 4 capex lines, all `source='manual'`.
Consequence, verified: **Site DD capex does not reach Underwriting**, so
the rate bug never touched equity, IRR or equity multiple. It wants a
cleanup pass, or an implementation.

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

**Assessment 11 is Michelle's live work.** Nabob Hill, inspector MJ,
2026-08-16, one unit, one kitchen, 23 findings. Read-only, always. Its
`property_label` created a 12th entry in the notetaker property registry
and **does not resolve to Deal Dive deal 2 (1120 Jackson Street)**, which
is plausibly the same building. One alias row would merge them —
`site_dd_assessments.deal_id` is `None` on all three assessments and
nothing populates it. Blocked on her confirming they are the same
property.

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
