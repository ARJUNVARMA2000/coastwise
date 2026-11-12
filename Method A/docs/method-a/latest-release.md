# Latest Method A release — 2026-11-12

[Coastwise](https://coastwise-production.up.railway.app) publishes the newest one-shot output and source PDF for each of the ten Brooklyn reports (2015–2018 and 2020–2025). Superseded current data was removed from working files, both repository app folders and hosted storage. Git history and unrelated shared-repository folders remain intact.

## Implementation

Before: separate header, page extraction and later grading calls, extraction-only timing and historical datasets mixed into the working experience.

After: PyMuPDF embedded text with absolute PDF pages → 24,000-character chunks with up to 1,500 context characters on each side → at most four concurrent DeepSeek requests, one combined extraction/provisional-grade call per chunk → lightweight local evidence/rule/component/material checks and merging → existing tables, PDF evidence and CSV/JSON/Excel/ZIP. There are no default retry, repair, separate grading or judge passes. Scans and unreliable layout remain limitations.

The UI offers ten complete report selections rather than treating the output's 110 physical structure/inspection branches as separate reports. Original linked-table identifiers and shared evidence/context are retained. Original ratings remain separate from project grades. Missing findings and uncertain grades never imply sound condition.

## Actual execution

The previously paid 2016 output was reused. Nine new reports ran once on `deepseek/deepseek-v4.1-flash-1017` via `deepinfra/fp8`, with 12,000 output tokens and reasoning disabled. New processing used 27 requests, 490,373 tokens and **$0.06897713144** provider-billed inference, with $0 held/unknown, below the $1 hard cap. The separate ledger records $0.068977139 because each request settles upward to nanodollars. Including the reused 2016 result, the retained dataset represents 29 calls and $0.07338281944; its earlier charge was not repeated.

The new batch took **733.347 seconds (12 minutes 13 seconds)** including a safe checkpoint pause. Per-report server times include local validation and saved exports; they are not browser upload-to-complete measurements.

| Report | Defects | Calls / valid chunks | Server seconds |
| --- | ---: | ---: | ---: |
| 2015 | 29 | 2 / 2 | 24.150 |
| 2016, reused | 38 | 2 / 2 | 31.435 |
| 2017 | 38 | 2 / 2 | 34.859 |
| 2018 | 11 | 2 / 2 | 14.522 |
| 2020 | 18 | 2 / 2 | 22.536 |
| 2021 | 17 | 2 / 2 | 55.511 |
| 2022 | 39 | 2 / 2 | 70.137 |
| 2023 | 107 | 3 / 3 | 127.597 |
| 2024 | 132 | 6 / 4 | 161.109 |
| 2025 | 95 | 6 / 4 | 146.696 |

The 2016 browser measurement was 32.868 seconds with partial coverage. **The complete full-report two-minute target is not established.** Dense/larger reports took longer; low native-text coverage and partial results cannot count as target success.

## Published limitations

Source review found 42 of 90 major finding groups fully retained, 46 partial and two missing. Material omissions, component ownership, repaired/current labels and measurement errors remain. Four 2024/2025 chunk responses were truncated and rejected. Only 2020 has complete native-text coverage, which does not establish extraction accuracy.

The dataset contains 524 defects, 363 component observations, 143 measurements and 2,292 projected citations. **All 524 project grades are uncertain**; no numeric grade passed the local rule/evidence checks. Some abstentions are overly conservative. Quote matching establishes text presence, not correct ownership or completeness.

The earlier source-fidelity release condition failed. After disclosure, the user explicitly authorized “Push and deploy latest outputs.” Global and per-report limitations appear in the UI and exports. Raw outputs were not silently repaired, and no additional inference ran for publication.

## Release and verification

Code/data commits: [personal `6f5aac0`](https://github.com/ARJUNVARMA2000/coastwise/commit/6f5aac038d2b3c39379ecdd6c8f3e9f2fba79425) in `Method A/`, and [shared `3f91e5a`](https://github.com/prksyng54/msds-capstone/commit/3f91e5ac597d8998529b7680d5e55beb618821e2) in `Method_A/`. Both CI runs passed: [personal CI](https://github.com/ARJUNVARMA2000/coastwise/actions/runs/43159482061), [shared CI](https://github.com/prksyng54/msds-capstone/actions/runs/43159476463).

Railway deployment `0cc028d0-ab28-454e-b0b9-3c0835e8c39c` succeeded. Dataset release ID: `4d0d9de280d8739b08e74322d9cb3bdf78c82d219271c30dd32f8ab047d3faad`. Startup verifies the bundled data before replacing superseded hosted files; the same release is idempotent and does not erase later uploads on restart.

Validation: 36 current offline tests, Ruff and JavaScript/shell syntax checks passed; complete local report tables and limitations were inspected in the browser. Authenticated live API checks verified all ten report aggregates, all source PDF hashes, all 524 defects, one quotation highlight/rendered page per report, per-report JSON and complete CSV/JSON/Excel/ZIP record parity. Authentication remained enforced. Superseded endpoints and a known old run returned 404/410. No model calls were used for these checks.

Working-file cleanup removed 20,403 superseded or duplicate files, totaling 3,493,974,332 bytes. Ten terminal outputs/PDFs and the current spending ledger remain; deletion records contain metadata, without retaining old data backups.

## Existing Feature importance included by request

The user explicitly approved restoring the existing Feature importance page and its one saved analysis snapshot. This is the latest saved analysis from October 29, 2026: Claude scored 25 reports; 20 reports across 11 structures and 26 features enter the charts. The page shows its generation date and explains that these report-level scores are separate from the new Brooklyn defect outputs. No analysis was recomputed and no model request was made. Superseded copies of this snapshot were removed; the current page/data remain.

Offline checks verified CSV/JSON consistency (20 analyzed plus five excluded equals 25 scored), feature counts and references, saved prediction/error arithmetic, navigation/assets and JavaScript syntax. The browser rendered the charts, report table and provenance; the model-variant control updated the saved chart. All 36 current offline tests passed with the restored route.

The approved Feature importance addition is deployed with the same ten-report dataset release. Final authenticated hosted checks verified its page/provenance/navigation, exact saved JSON/CSV bytes and script/style assets, then rechecked all ten report/source/evidence/export APIs and authentication. The deployment and CI identifiers above refer to this final application version. No inference ran during these checks.
