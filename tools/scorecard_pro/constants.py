"""Scorecard Pro — module-level constants and category mappings (verbatim)."""


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_INDEX = {month: idx + 1 for idx, month in enumerate(MONTHS)}
ALLOWED_PNL_EXT = {".csv", ".xlsx", ".xlsm"}
ALLOWED_SCORECARD_EXT = {".xlsx", ".xlsm"}
MAX_PENDING = 8


# OXPT-specific write-back category mapping (Michelle's explicit decisions).
# Maps existing Scorecard T12 row labels — already present in the sheet,
# never newly created or renamed — to the P&L account code(s) whose values
# should be summed into that row. Scoped to OXPT only (see the property-name
# check in ScorecardUpdater.update()); must not affect Eagle Rock or Canyon.
_OXPT_ROW_GROUPS = {
    "total office expense": ["6100"],                # Administration Costs ("G&A")
    "total legal & professional fees": ["6200", "4418"],  # Legal & Professional + Attorney/Court Fees (4418 sits
                                                       # under Other Income in the P&L, but Michelle wants it
                                                       # tracked against Legal regardless of that placement)
    "total advertising": ["6300"],                    # Marketing & Leasing ("Marketing")
    "total payroll expense": ["6400"],                 # Salaries & Payroll
    "total cleaning & trash removal": ["6520"],        # Contract Services: housekeeping-specific
    "total outside contractors": ["6540", "6545", "6555", "6565"],  # Contract Services: trades
    "total other expenses": ["6560"],                  # Contract Services: pest control
    "total repairs": ["6600", "6700"],                  # Maintenance Related + Turnover Costs (6700 is a
                                                        # Repairs & Maintenance subcategory, not its own category)
    "total grounds & lawn maintenance": ["6800"],       # Grounds
    "total utilities": ["6900"],                        # Utilities
    "total insurance": ["7120"],                        # Insurance
    "total taxes": ["7130"],                            # Property Taxes
    "asset mgmt fee": ["7210"],                         # Asset Management Fee — kept in its existing row/location
    "total management fees": ["7220", "7250"],          # Management Company Charges minus Asset Mgmt Fee
    "total debt service": ["7300"],                     # Debt Service
    "total capital expenses": ["7500"],                 # Big-ticket capital repairs
    # Other Income (4300 family) splits across several existing rows rather
    # than one bucket — fee-type income to the relevant Fees row, utility
    # reimbursement to Utility Recovery, parking-related to Parking Income.
    "electric": ["4320"],
    "late fees": ["4400"],
    "insurance services": ["4402"],
    "admin fee": ["4405"],
    "application fee income": ["4415"],
    "cleaning fee": ["4420"],
    "early termination fee": ["4448"],
    "month-to-month fee": ["4450"],
    "damages": ["4452"],
    "pet fee-non refundable": ["4455"],
    "nsf fees collected": ["4460"],
    "parking income": ["4508"],
    # Oddball Other Income lines with no dedicated row of their own — verified
    # against real OXPT data as revenue (all three post positive values nested
    # under 4300 Other Income), so they land in the existing catch-all fee
    # row alongside the ten name-matched lines below, rather than getting a
    # new row each. 4295 (Miscellaneous Credit) and 4580 (High Risk Fee, a
    # sibling income line outside the 4000/4300 tree) join the same bucket.
    "miscellaneous fees": ["4341", "4470", "4500", "4295", "4580"],
    # Rents family (4000/4100/4200 children) — the rollups themselves
    # (4000, 4100, 4200) are excluded below since these children fully
    # capture their values.
    "loss/gain to market": ["4120"],           # "(Loss) / Gain to Old Lease" — best-fit match despite wording
    "delinquency": ["4210"],                   # Bad Debt / Write-Off Uncollectable Rent
    "vacancy": ["4220"],                        # Vacancy Loss
    "concessions": ["4250", "4258", "4260"],    # Rent Concessions + Utility Credit + Rent Discount
}

# Other Income detail lines with no assigned P&L code known ahead of time —
# matched by account name (case-insensitive) rather than code, and routed to
# the same "Miscellaneous Fees" row as the codes above.
_OXPT_MISC_FEE_NAMES = {
    "auto charge",
    "auto payment fee",
    "charge off recovery",
    "corporate housing",
    "furniture rental income",
    "housing assistance payment",
    "insurance - waived",
    "insurance – waived",
    "move in charge",
    "renters insurance income",
    "storage income",
}

# Codes whose value is already fully captured by a group above (parent
# rollups distributed via children, or children merged into a parent
# rollup via a group) — excluded from digit/name-based matching so they
# can't also write into the same or a different cell a second time.
_OXPT_EXCLUDED_CODES = {
    "6000", "7000",  # grand rollups, fully distributed via sub-categories
    "6112", "6113", "6115", "6118", "6138", "6139", "6140", "6157", "6164", "6178", "6187",  # 6100 children
    "6205", "6210",  # 6200 children
    "6305", "6315", "6350", "6355", "6360",  # 6300 children
    "6405", "6415", "6430", "6450", "6465",  # 6400 children
    "6500",  # Contract Services rollup, distributed via children individually
    "6606", "6627", "6636", "6639", "6651", "6654", "6660", "6663", "6669", "6675",  # 6600 children
    "6810",  # 6800 child
    "6910", "6911", "6915", "6930", "6940", "6955", "6960",  # 6900 children
    "7200",  # Management Company Charges rollup, distributed via 7210 + [7220, 7250]
    "7330", "7350",  # 7300 children
    "7502", "7505", "7511", "7516", "7518", "7520", "7536", "7543", "7544", "7545",
    "7547", "7549", "7550", "7556", "7560", "7564", "7568", "7570", "7573", "7578", "7595",  # 7500 children
    "4300",  # Other Income rollup, distributed via children individually
    "6750",  # 6700 (Turnover Costs) child, folded into "total repairs" via the 6700 code above
    "7400",  # Other Expenses rollup — its only known child (7210, Asset Mgmt Fee) is mapped separately
             # above and kept below the NOI line; no other 7400 child has appeared in any real OXPT
             # export seen so far. If one does, it will surface here as unmatched rather than being
             # silently miscounted — a genuine "Misc Expense" row should be added against real data
             # at that point, not built speculatively now.
    "4000", "4100", "4200",  # Rents rollups (Net Rental Income / Gross Possible Rent / Deductions),
                             # fully distributed via their 4110/4120/4210/4220/4250/4258/4260 children.
    # 7110 Replacement Reserve Escrow is deliberately NOT excluded or mapped here — no existing T12
    # row fits it (it's a peer to Insurance/Taxes/Management Fees, not nested under any of them), and
    # per the precedent already set for Eagle Rock/Canyon, genuinely homeless items are left flagged
    # as unmatched rather than getting a new row invented for them.
}

# Eagle Rock's Scorecard T12 sheet mirrors the P&L's own tree structure (every
# "Total X" rollup gets its own code-prefixed row, e.g. "6100 Total
# Administration Costs"), which the existing code-prefix scan already matches
# directly — no explicit row-group mapping is needed the way OXPT required.
# The only gap is leaf-level detail codes whose parent rollup IS matched but
# who don't get their own separate line; excluding them here (so they don't
# show as unmatched) is safe because their dollar value is already fully
# captured in that matched parent total. Verified against the real Eagle
# Rock T12 (annual figures): each child's parent total equals the sum of its
# section's children exactly, including 7330 Mortgage Payment, which is
# fully captured by "7300 Total Debt Service" ($18,847.04/month = $6,460.10
# Mortgage Payment + $12,386.94 Mortgage Interest for May 2026, matching
# exactly) — unlike OXPT's 7110, this is not a homeless financing item, so
# it does not need a below-NOI or new-row decision.
_EAGLE_ROCK_EXCLUDED_CODES = {
    "4490",  # 4300 (Other Income) child
    "6125", "6128", "6169",  # 6100 (Administration Costs) children
    "6639",  # 6600 (Maintenance Related) child
    "6780",  # 6700 (Turnover Costs) child
    "7551",  # 7500 (Repairs) child
    "7330",  # 7300 (Debt Service) child — captured by "7300 Total Debt Service", already matched
}

# Canyon's Scorecard T12 sheet uses the same mirrored-tree structure as Eagle
# Rock (confirmed against the real file), in an unrelated older "12 Month
# Rolling" report's columns (1-14) sitting alongside the real account-code
# region (column 16+) that the parser actually scans — the two must not be
# confused. Same logic as Eagle Rock: leaf children of an already-matched
# parent rollup are excluded here since their value is already captured.
# 6800 (Grounds) and its only child 6810 (Landscape - Maintenance) are
# deliberately NOT excluded — verified no "6800 Total Grounds" row (or any
# other row referencing 6800/6810/Grounds/Landscape) exists anywhere in this
# Scorecard, so unlike the rest of this list, that one dollar amount
# ($17,561.50/yr) isn't captured anywhere and is left flagged as unmatched.
# 4341/6320/6370/6605/6785/7544/7595 were added later -- newer, more
# granular leaf accounts a subsequent T12 P&L export broke out under the
# same already-matched parent rollups (4300/6300/6600/6700/7500) as
# everything else in this set; not a new gap.
_CANYON_EXCLUDED_CODES = {
    "4305", "4428", "4470", "4341",  # 4300 (Other Income) children
    "6106", "6178", "6184", "6187",  # 6100 (Administration Costs) children
    "6330", "6335", "6343", "6320", "6370",  # 6300 (Marketing & Leasing) children
    "6415",  # 6400 (Salaries & Payroll Related) child
    "6530", "6545",  # 6500 (Contract Services) children
    "6606", "6612", "6636", "6642", "6654", "6605",  # 6600 (Maintenance Related) children
    "6740", "6780", "6785",  # 6700 (Turnover Costs) children
    "6905",  # 6900 (Utilities) child
    "7240",  # 7200 (Management Company Charges) child
    "7502", "7518", "7534", "7536", "7537", "7541", "7564", "7570", "7573", "7544", "7595",  # 7500 (Repairs) children
}
