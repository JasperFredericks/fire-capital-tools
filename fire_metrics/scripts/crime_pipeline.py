#!/usr/bin/env python3
"""Consolidated crime-index pipeline.

Chains the four previously-separate crime scripts into one function call:

    build_crime_index            (add_crime_index.py)
    -> fix_crime_non_fl_overrides   (fix_crime_non_fl_overrides.py)
    -> fix_crime_manual_rating_fills (fix_crime_manual_rating_fills.py)
    -> integrate_crime_into_clean_cities (integrate_crime_into_clean_cities.py)

These four steps were always a single logical pipeline (each one's real
purpose only makes sense operating on the previous one's "Crime Index"
sheet), but were wired together by hand through a sequence of differently
-named intermediate files that nothing in the codebase actually produced
for the next step by default -- running them required manually renaming
files between each invocation. This module does the same four steps, with
the same underlying matching/scoring/override/fill logic, unchanged, but
chains them internally through a temp directory instead.

Crime data itself stays manual/periodic: this pipeline still requires a
manually-downloaded FBI Table 8 workbook uploaded through Admin Data Tools
or configured with FBI_CRIME_WORKBOOK_PATH (see add_crime_index.py's module
docstring) -- there is no live crime API to call instead.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from add_crime_index import build_crime_index, INPUT_FILE as CRIME_INDEX_INPUT_FILE
from fix_crime_non_fl_overrides import fix_crime_non_fl_overrides
from fix_crime_manual_rating_fills import fix_crime_manual_rating_fills
from integrate_crime_into_clean_cities import integrate_crime_into_clean_cities

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_ALL_METRICS_CLEAN.xlsx"


def run_crime_pipeline(input_path=None, output_path=None, fbi_file=None, sample=None):
    """Run the full crime-index pipeline in one call.

    input_path: workbook with a "Clean Cities 100k+" sheet to build the
        crime index from (defaults to add_crime_index.py's own default --
        the early population/landlord-stage workbook).
    output_path: final workbook, with both a corrected "Crime Index" sheet
        and the decision-useful crime columns merged onto "Clean Cities
        100k+".
    fbi_file: the manually-downloaded FBI Table 8 workbook (defaults to
        add_crime_index.py's FBI_CRIME_WORKBOOK_PATH resolver).
    sample: passed through to build_crime_index (limits to N cities, 1 per
        state, for a quick test run).

    Returns a dict with the final output path and each step's own summary
    dict, so a caller can inspect what happened at any stage without
    needing to have called each step itself.
    """
    input_file = Path(input_path) if input_path is not None else CRIME_INDEX_INPUT_FILE
    output_file = Path(output_path) if output_path is not None else OUTPUT_FILE

    with tempfile.TemporaryDirectory(prefix="crime_pipeline_") as tmp_dir:
        tmp = Path(tmp_dir)

        crime_index_result = build_crime_index(
            input_path=input_file,
            output_path=tmp / "01_crime_index.xlsx",
            fbi_file=fbi_file,
            sample=sample,
        )

        overrides_result = fix_crime_non_fl_overrides(
            input_path=tmp / "01_crime_index.xlsx",
            output_path=tmp / "02_non_fl_fixed.xlsx",
            backup_path=tmp / "02_backup.xlsx",
            fbi_file=fbi_file,
        )

        fills_result = fix_crime_manual_rating_fills(
            input_path=tmp / "02_non_fl_fixed.xlsx",
            output_path=tmp / "03_manual_fills.xlsx",
        )

        output_file.parent.mkdir(parents=True, exist_ok=True)
        integration_result = integrate_crime_into_clean_cities(
            input_path=tmp / "03_manual_fills.xlsx",
            output_path=output_file,
        )

    return {
        "output_path": str(output_file),
        "crime_index": crime_index_result,
        "non_fl_overrides": overrides_result,
        "manual_rating_fills": fills_result,
        "integration": integration_result,
    }


def main():
    result = run_crime_pipeline()

    print("=" * 60)
    print("Crime pipeline complete")
    print("=" * 60)
    print(f"Output file path: {result['output_path']}")
    print()
    print("-- Step 1: build_crime_index --")
    print(f"Total cities: {result['crime_index']['total_cities']}")
    print(f"With crime data: {result['crime_index']['with_crime_data']}")
    print(f"Needs manual review: {result['crime_index']['needs_manual_review']}")
    print()
    print("-- Step 2: fix_crime_non_fl_overrides --")
    print(f"Non-Florida blank rows before: {result['non_fl_overrides']['non_fl_blank_before']}")
    print(f"Non-Florida rows successfully populated: {len(result['non_fl_overrides']['populated'])}")
    print(f"Non-Florida rows still blank: {result['non_fl_overrides']['remaining_blanks']}")
    print(
        f"Florida blank rows left untouched: {result['non_fl_overrides']['fl_unchanged']} "
        f"(of {result['non_fl_overrides']['fl_blank_before']} tracked)"
    )
    print()
    print("-- Step 3: fix_crime_manual_rating_fills --")
    print(f"Manual rating fills added: {len(result['manual_rating_fills']['updated_cities'])}")
    print(f"Rows highlighted: {result['manual_rating_fills']['highlight_count']}")
    print()
    print("-- Step 4: integrate_crime_into_clean_cities --")
    print(f"Clean Cities 100k+ rows: {result['integration']['total_rows']}")
    print(f"Matched to Crime Index: {result['integration']['matched']}")
    print(f"Not matched: {result['integration']['unmatched']}")
    print(f"Manual rating fill rows copied: {result['integration']['manual_fill_copied']}")


if __name__ == "__main__":
    main()
