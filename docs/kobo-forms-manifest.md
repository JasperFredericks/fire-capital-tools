# Reference inspection instruments — manifest

**Received 2026-08-18 from Paresh Patel, Zuna Investments.**

Four KoboToolbox files: two mature production inspection forms and the
two data files that drive them. They were in real production use before
the Site DD rebuild.

---

## The files are NOT in this repository

They are a third party's production work product. **This repository is
public** — verified live, `private: false`, anonymous API reads return
200 — so committing them would publish Paresh's instruments and Zuna's
operational documents to the open internet, permanently. Git history
makes that effectively irreversible: deleting the files in a later commit
does not remove them from the history, and making the repository private
afterwards does not un-publish what was already fetched or forked.

That is not a decision to take on a third party's behalf as a side effect
of wanting a backup.

**This manifest is metadata only.** It exists so that any copy found
later can be verified as the same file, and so that the existence and
provenance of these instruments is recorded somewhere durable even while
the files themselves live elsewhere.

**Where they should live** — in preference order, pending Michelle:

1. A private repository (e.g. `firecapitaltools/inspection-reference`).
   Version-controlled, diffable, access-controlled.
2. This repository, *after* it is made private. That is already an open
   item; it is a deliberate decision with deploy implications, not
   something to do as a storage workaround. See
   `docs/repo-private-preflight.md`.
3. Michelle's Google Drive. The organisation already uses Google.

As of writing, the only copies are in a browser Downloads folder on one
machine.

---

## Contents

| file | sha256 | bytes |
|---|---|---|
| `The_View_Inspection_XLSForm_v7.xlsx` | `16abb1200601633dc0aad7009a9db7eb272d62161d45a5364d01ade8d4d00242` | 31,691 |
| `The_View_Building_Exterior_Inspection_v5.xlsx` | `81d20a4453ce2473fe2b303f909955d8815e28c7a843a801bc467e56a032e575` | 34,637 |
| `rent_roll.csv` | `7044437aa1c077342b5f81f92c124ae603f15a9ecc9ac1f4a81a3cf022770f1c` | 7,972 |
| `property_config.csv` | `5a722355143cd37c9cdbd76093c683b673f9d1125412125d819256b5b1507afb` | 120 |

**`The_View_Inspection_XLSForm_v7.xlsx`** — KoboToolbox XLSForm for a
unit inspection. 344 survey rows, 35 choice lists, 3 sheets
(survey/choices/settings). Roughly 25 of the choice lists are
item-specific condition scales where the condition value *is* the scope
of work (closet: Replace Rod / Replace Shelves / Replace Rod and
Shelves). Hydrates unit data from `rent_roll.csv` via 12 `pulldata`
fields, then asks the inspector to confirm. Blocks re-submission for a
unit already inspected, via a `count()` calculate.

**`The_View_Building_Exterior_Inspection_v5.xlsx`** — exterior
inspection. 672 survey rows, hardcoded per floor, elevation and condenser
unit; a building with a different floor count needs a new form. The
content is valuable; the structure is not the one to copy.

**`rent_roll.csv`** — 84 units for The View at Pembroke. Bed and bath
counts are **pre-derived** into separate integer columns
(`num_bedrooms`, `num_full_bath`, `num_half_bath`), so this file
sidesteps bed/bath string parsing rather than solving it. Carries per-bed
occupancy packed into one column.

**`property_config.csv`** — three rows. `property_name`,
`smoke_detector_requirements`, `co_detector_requirements`. Drives whether
per-unit questions are asked at all.

---

## PII: assume yes on any future export, regardless of this sample

`rent_roll.csv` has a column named **`resident_name`**. In this
particular export it contains **bed occupancy statuses**, not names —
all 84 rows read like `A:Occupied; B:Vacant Ready; C:Vacant Ready;
D:Occupied`. Checked row by row, not sampled. Neither XLSForm contains
any email address or phone number.

**Do not conclude from this that the file format is safe.** The column is
*named* `resident_name`, which is a strong signal that the source system
populates it with actual residents and that this export happens not to.
Other rent rolls already handled by this project do carry real tenant
names in the clear — the Jackson and Oxford Pointe files both do.

So: **any future export from this system must be treated as containing
PII until inspected**, and inspected per-row rather than assumed from
this manifest. A storage or ingestion process built around these files
should be designed for personal data even though this instance carries
none.
