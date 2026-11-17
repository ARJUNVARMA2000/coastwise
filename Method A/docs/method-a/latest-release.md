# DSNY 2020–2025 — Method A

Six Southwest Brooklyn DSNY PDFs were processed with the same high-reasoning Method A model, prompt, schema and grading rules as the previous showcase. Method B is owned by the team and is outside this delivery.

Model: DeepSeek V4.1 Flash 1017; pinned DeepInfra FP8; high reasoning; temperature 0; 24,000-character primary chunks initially; up to 1,500 adjacent-context characters per side; 393,216 maximum output tokens per request; four active requests globally. One extraction-and-grade call per effective chunk. No OCR, vision, separate judge or model repair stage.

The first pass made 21 requests: 17 accepted, one rejected at the provider output limit and three interrupted by an unrequested one-hour report cutoff. The user corrected this to no deadline. The 17 accepted originals were reused; only failed primary ranges were subdivided for extraction without an elapsed-time cutoff. First-pass results and unknown charge reservations are preserved. Recovery phases and source boundaries are recorded explicitly below.

| Year | Pages | Valid chunks | Records | Numeric / uncertain grades | Job seconds* | Known billed USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2020 | 12 | 2/2 | 8 | 0 / 8 | 129.0 | $0.009375 |
| 2021 | 10 | 2/2 | 32 | 0 / 32 | 245.3 | $0.014527 |
| 2022 | 20 | 2/2 | 42 | 0 / 42 | 528.6 | $0.020289 |
| 2023 | 52 | 3/3 | 121 | 0 / 121 | 554.5 | $0.035007 |
| 2024 | 101 | 14/14 | 813 | 0 / 813 | 8038.4 | $0.256664 |
| 2025 | 95 | 16/16 | 427 | 0 / 427 | 8363.4 | $0.183893 |

Recovery `recovery-no-deadline`: 20 requests; at most 6,000 primary source characters per child; no elapsed-time cutoff. Accepted inputs were reused.

Recovery `recovery-refined-2`: 3 requests; at most 2,000 primary source characters per child; no elapsed-time cutoff. Accepted inputs were reused.

Batch wall time: 8364.4 seconds. Known billed: $0.519756. Held/unknown: $0.368937. Total committed: $0.888693 under the $30 cap. Maximum active requests observed: 4.

*Each report job time includes its wait for the shared request pool, local validation and exports. It is not standalone model latency or browser upload-to-render time. Per-request timings and token usage are retained in the saved traces and summary CSV.

For 2024 and 2025, elapsed time runs from the initial report job through recovery, including preparation/waiting between phases. Reported token totals include only returned usage; interrupted requests may have additional unknown usage. Held charges are conservative full-output reservations, not confirmed additional billing.

Each year folder includes the exact source PDF and matching CSV, JSON and Excel tables with evidence, source-stated ratings and provisional grades or uncertainty. IDs are report/run scoped. Records are machine findings, not counts of unique damaged members. Raw outputs are preserved; review annotations do not repair them.

Integrity checks reproduced the local merge, verified source/prompt/schema hashes and reconciled every CSV/Excel cell. Quote matching establishes native-text presence, not semantic accuracy or completeness. Scans, photographs and table reading order can leave findings or measurements absent. Unsupported grades remain uncertain. Missing findings do not imply sound condition. See integrity-review.json and source-spot-checks.json for per-year qualifications.

The authorized Coastwise release replaces the previous Alameda/Berkeley/DSNY-2016 showcase with only these six saved DSNY results. See the separate release verification record for publication status. Team-authored pages and Method B are outside this data replacement.

Published-service target: https://coastwise-production.up.railway.app/extraction. The team’s merged review pages from commit 9213ed0 are preserved byte-for-byte. Saved source archives and Git history remain available; the old three-report runtime collection is replaced.
