# Address normalize-at-entry, and the four duplicate cache rows

**Investigated 2026-08-20 (Part 38 Step B). Read-only against production.
Nothing was written or deleted. RentCast usage unchanged at 16/50.**

The Part 23 decision was: *do not change `normalize_address_key`;
canonicalise at entry instead, when a deal is created or edited, one
address at a time with a human present; plus a one-off merge of the four
duplicate rows.*

The constraint holds and is not revisited here. What follows is why the
**rest** of that plan does not survive contact with the data, and what to
build instead.

---

## 1. Normalising deal entry would have prevented none of the four duplicates

Production holds **two deals**:

| deal | address | city | zip |
|---|---|---|---|
| 1 | `19 Bay Vista Drive` | Mill Valley | `94941-1604` |
| 2 | `1120 Jackson Street` | San Francisco | `94133` |

Neither is Steiner. Neither is Belvedere. **Ten of the twelve cached rows
correspond to no deal at all.**

The duplicates came from the **Rent Comps standalone search box**.
`rent_comps._context_from_request()` falls through to a free-text address
from the query string or form whenever no `deal_id` is supplied, and that
text goes straight into `normalize_address_key`. A deal record was never
involved, so a canonicaliser bolted onto `new_deal()` and `edit_deal()`
would have run zero times against the addresses that actually collided.

**The entry point that needs the work is the one nobody named.**

## 2. And it still could not have merged these particular pairs

`24 Steiner` versus `24 Steiner Street` differ by exactly the street-type
suffix. Merging them is **suffix normalisation**, which is the one
transformation Part 23 ruled out, for the reason that still stands:
`100 Main St`, `100 Main Ave` and `100 Main Blvd` are three real
addresses that all collapse to `100 main`.

So the approved fix and the observed problem do not meet. No
transformation that is safe to apply can merge these pairs, at entry or
anywhere else. **A transformation is the wrong instrument.** See section 5.

## 3. What each pair actually contained

The Part 23 caveat was the right question to ask: if the two rows resolved
to different subject units they are two different wrong answers, not
duplicates, and that is worth knowing before one is discarded.

**Both pairs hold equivalent answers.** Same rent estimate, same range,
same fifteen comparables, same subject resolution.

| | `24 steiner` | `24 steiner street` |
|---|---|---|
| fetched | 2026-08-18T19:05 | 2026-08-19T04:16 |
| rent estimate | 3970 | 3970 |
| range | 2960 - 4980 | 2960 - 4980 |
| subject property | **all fields null** | **all fields null** |
| comparables | 15 | 15, same set |

| | `598 belvedere` | `598 belvedere street` |
|---|---|---|
| fetched | 2026-08-18T18:59 | 2026-08-18T18:32 |
| rent estimate | 7940 | 7940 |
| range | 4540 - 11330 | 4540 - 11330 |
| subject property | 5bd/6ba, 2846 sqft, 1922, Single Family | **identical** |
| comparables | 15 | 15, same set |

The stored JSON hashes differ, and every difference is an artefact of the
two fetches happening at different moments rather than a disagreement
about the property:

* **Steiner** - `days_old` is larger by exactly 1 on 12 of 15 comps (the
  fetches are a day apart); one `correlation` differs by 0.0001.
* **Belvedere** - `distance_miles` differs in the fourth decimal on all
  15 (the subject geocode moved by a few feet between calls); comps 12
  and 13 swap places, on a correlation tie of 0.6255 versus 0.6256.

**Belvedere is a confirmed duplicate**: both rows resolved to the same
real property record, which is a strong identity signal.

**Steiner is equivalent but unresolved**: RentCast matched *no* subject
property for either row, so both hold an area-level estimate rather than
an answer about that building.

### The comparator that lied, and the control that caught it

The first comparison keyed comparables on `id` and reported "identical".
There is no `id` field in a cached comparable - the keys are `address`,
`bathrooms`, `bedrooms`, `correlation`, `days_old`, `distance_miles`,
`listing_status`, `price`, `square_footage`. Every `.get("id")` returned
`None`, so the check compared `[None] * 15` against itself and would have
reported "identical" for two unrelated comp sets.

Re-keyed on `address`, it was then positive-controlled against a
known-different address - and **the control failed**:

```
22 steiner st  VS  24 steiner  ->  same comp set: True,  overlap 15 of 15
                                   same rent estimate, same range
                                   same distances to 4 decimals
```

Two different buildings, indistinguishable in the cache. So **comp-set
identity is not evidence that two rows describe the same address**, and
the equivalence claimed above rests on the subject resolution and the
estimate, not on the comparables.

That is also a small confirmation of the RentCast disclosure work: where
RentCast resolves no subject, the comparables are not matched to the
subject at all - they are an area sample, and every address on the block
gets the same one.

## 4. A live defect the investigation turned up: ZIP+4 orphans deal 1

`normalize_address_key` concatenates the zip verbatim. Deal 1 stores
`94941-1604`; its cached row was created from a ZIP5 entry:

```
deal 1 key   '19 bay vista drive mill valley ca 94941-1604'
cached row   '19 bay vista drive mill valley ca 94941'      -> MISS
```

**Opening Rent Comps for deal 1 cannot hit its own cached data and will
spend a fresh RentCast call**, with valid data sitting in the table. Deal
2's key matches its row, so this is one deal today - but it is the only
deal carrying a ZIP+4, which is the point: the defect is in the shape of
the input, not in that deal.

This one is worth fixing and is unrelated to street suffixes. Note that
truncating the zip to five digits **inside the key function** would orphan
nothing - none of the twelve rows carries a ZIP+4 - so the invalidation
arithmetic that protects the suffix rule does not protect this one. It is
still a change to `normalize_address_key`, which is out of scope by
instruction, so it is raised rather than made.

## 5. What to build instead

The transformation that would fix the observed duplicates is unsafe. The
transformation that is safe fixes nothing that has actually gone wrong.
So the proposal is **not a transformation**.

**Show near-matches at entry, and let the human pick.** One address at a
time, a person present - exactly the Part 23 framing, with the decision
left where it can be made correctly:

> You looked up **24 Steiner Street, San Francisco CA 94117** on 19 Aug.
> Is this the same property?   [ Use that one ]   [ No, this is different ]

Suffix-insensitive matching is safe *as a search* precisely where it is
unsafe *as a key*: `100 Main St` would offer `100 Main Ave` as a
candidate, a human would say no, and nothing would be merged. The
collision that makes the transformation wrong makes the suggestion
merely occasionally unhelpful.

Applied at **both** entry points, standalone Rent Comps included, since
that is where every observed duplicate was created.

### The exact transformation, for the part that is still a transformation

Applied to the address line only. City and state are untouched beyond the
existing `.strip()` / `.upper()`.

| # | change | visible to user? | can it alter deliberate input? |
|---|---|---|---|
| 1 | collapse internal whitespace runs to one space; strip ends | no - shown identically by every renderer | **no**. Cannot change meaning. |
| 2 | strip trailing `.` and `,` from each token: `St.` to `St` | **yes, shown for confirmation** | only cosmetically; still confirmed |
| 3 | ZIP+4 to ZIP5 **for lookup only**, stored zip untouched | **yes, stated** | **no** - the typed ZIP+4 is kept on the deal |
| - | street-type suffixes | **not touched** - ruled out in Part 23 | - |

Only (1) is applied silently, and it is silent because it cannot change
what the address means. **Everything else is shown before it is stored.**
That is the answer to "whether it can silently alter something the user
typed deliberately": by construction, no.

## 6. The cleanup is deliberately not done

Nothing was deleted, and the recommendation is **not to delete yet**.

The pairs are equivalent, so a merge would lose no information. But the
cleanup as specified has **no benefit left to collect** - the four calls
are already spent, and deleting rows reclaims nothing. It carries a small
cost in the wrong direction: whichever form of the address is not kept
becomes a cache miss, and a miss is a paid call against 16/50.

Which row to keep is decided by which form the entry flow settles on, and
that flow is not built. **Do the merge with the normaliser, not before
it**, and keep the row whose key matches the canonical form.
