# Current Method A release

The showcase contains Alameda 2017, Berkeley 2017 and DSNY 2020–2025: eight reports, 949 PDF pages and 2,597 provisional records. Alameda and Berkeley are restored from their exact previously published saved runs. DSNY 2016 remains excluded. This restoration makes zero model calls and adds no inference cost.

| Report | PDF pages | Accepted chunks | Provisional records |
| --- | ---: | ---: | ---: |
| Alameda 2017 | 381 | 19/20 | 376 |
| Berkeley 2017 | 278 | 14/15 | 778 |
| DSNY 2020 | 12 | 2/2 | 8 |
| DSNY 2021 | 10 | 2/2 | 32 |
| DSNY 2022 | 20 | 2/2 | 42 |
| DSNY 2023 | 52 | 3/3 | 121 |
| DSNY 2024 | 101 | 14/14 | 813 |
| DSNY 2025 | 95 | 16/16 | 427 |

All 327 saved source, response, result and export files are reused byte-for-byte. Report identities, measured extraction times, usage, review annotations and warnings are preserved. All project grades remain uncertain. Schema-valid extraction does not establish completeness or source accuracy. Record counts are not counts of unique damaged members.

Alameda PDF pages 121–131 and Berkeley PDF pages 231–253 retain their original failed-extraction gaps. No failed or accepted request is repeated. Native-text gaps, combined pile IDs, repeated findings, material-test/repair scope, measurement-kind and rating-ownership errors remain disclosed in report details and exports. The six DSNY reports retain their original source-review limitations, including missing detailed findings in DSNY 2020.

The DSNY 2020–2025 high-reasoning run used the same DeepSeek V4.1 Flash 1017 / DeepInfra FP8 route, temperature 0, prompt, schema and grading rules as the previous showcase. At most four requests ran concurrently. Failed source ranges were subdivided into 6,000-character and then 2,000-character children, retaining 1,500 adjacent-context characters per side and the original 393,216-token output allowance, with no elapsed-time cutoff. All 39 current DSNY chunks are accepted from 44 total attempts. Its budget remains $0.51975608 known billed plus $0.36893717 held for unknown original charges: $0.88869325 committed under $30. The previous three-report spending ledger is separate and unchanged.

No OCR, vision, separate judge or model repair stage was added. Restoration does not recompute Method B or the Answer key. Both team-authored pages and the separate Feature importance snapshot are preserved unchanged.

Publication target: the existing password-protected [Coastwise](https://coastwise-production.up.railway.app/extraction) service and both existing repository main branches. The release installer validates all source/run/file hashes and stages the full eight-report bundle before replacing the active runtime dataset. Credentials, project source archives and Git history are outside the replacement. Verification and final deployment receipts are retained under `output/coastwise/method-a-restored-alameda-berkeley-2026-11-16/` in the project workspace.

Local verification passed 51 application tests and Ruff. All eight report views, exact source PDF hashes, quotation evidence/rendered pages and CSV/JSON/Excel/ZIP exports were checked. All five DSNY 2016 report/source URLs remain unavailable. The full existing UI and team page assets are unchanged.

Verified publication: Railway deployment `300deb7b-e861-49aa-9f8a-d1beb8cb6bb8` succeeded with release `ffe4b584ea03992866acaf1bd5eee2596970a2f9c4828d8fd6b3c4066bad6177`. Both repositories’ application CI passed. Authenticated hosted readback verified all eight reports / 2,597 records, exact PDFs, quotation evidence/rendered pages and CSV/JSON/Excel/ZIP exports, all 16 existing UI assets and five excluded DSNY 2016 URLs returning 404. Deployment reused 98 files matching the committed application. Restoration added zero inference calls or cost.
