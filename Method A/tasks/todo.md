# Current Method A release

## Latest-only cleanup, both repositories and deployment — authorized 2026-11-11

The user confirmed: keep the newest Method A output and source PDF for each of all ten Brooklyn reports; delete superseded data from project working files, current repo branches and hosted storage; preserve Git history. The user then explicitly said “Push and deploy latest outputs,” overriding the earlier quality-based deployment hold. Known omissions, incorrect member/repair labels, uncertain grades and truncated chunks must remain visible. Do not rerun a model or rewrite Git history.

Push targets verified: public `ARJUNVARMA2000/coastwise` in its `Method A/` folder, and private `prksyng54/msds-capstone` in `Method_A/`. Keep unrelated shared-repo team folders unchanged.

- [x] Confirm deletion scope, two repository identities/access and explicit release override; inventory exact superseded data paths while protecting credentials, Git metadata, runtime prompts/rubric/schema, source code and latest outputs/PDFs.
- [x] Replace historical dataset loaders with latest-only reads, surface current quality limitations, and package the ten saved runs/PDFs for an idempotent deployment-time replacement of hosted data.
- [x] Delete the allowlisted superseded local datasets and remove obsolete data from both current repo trees, preserving Git history and unrelated team folders. Keep a cleanup record, not backups of deleted data.
- [x] Run current Method A, publication, evidence/export and UI checks; inspect the complete per-report tables locally.
- [x] Commit/push both cleaned app trees and update shared CI to the current Method A workflow; verify remote heads and checks.
- [x] Deploy the verified source/data to the existing Coastwise Railway service with authentication intact, replace superseded hosted records, verify latest ten reports, PDF evidence and downloads without inference, then record URLs/commits/deployment and actual cleanup/results.

### Release review — 2026-11-12

Completed the direct Method A implementation and ten-report release. Reused the paid 2016 result and processed the remaining nine reports once: 27 new requests, $0.06897713144 provider-billed, $0 held/unknown, below the $1 cap. The nine-report batch took 733.347 seconds including its safe checkpoint pause. No repair, retry, separate grading or judge requests ran. Complete full-report performance within approximately two minutes remains unestablished; dense reports took 128–161 server seconds.

The source-fidelity condition failed: 42 of 90 reviewed major finding groups were fully retained, 46 partial and two missing; member/repair/measurement errors remain. Four chunks were rejected for truncation. All 524 project grades remain uncertain. Publication followed the user's explicit override, with global and per-report limitations displayed and exported. No results were silently corrected.

Removed 19,797 superseded working files (3,431,707,169 bytes), plus 27 duplicate intake-only files (43,348,693 bytes) and 579 residual dataset/evidence/index files (18,918,470 bytes): 20,403 files / 3,493,974,332 bytes total. The current batch and runtime indexes each retain exactly ten terminal outputs and source PDFs. Deletion metadata and the latest spending ledger remain; Git history and unrelated shared-repo folders remain intact.

Code/data pushed to personal `ARJUNVARMA2000/coastwise` at `e7931e83c5deb5e514ab8e0fe662c681562de841` and shared `prksyng54/msds-capstone` at `715f882839dea14f03cdc9c480f6ab3425e86b95`. Both CI runs passed. Local validation: 36 current offline tests, Ruff and JavaScript/shell syntax checks; local browser report tables and limitations inspected. The local preview was stopped after publication.

Railway deployment `979926f6-7b93-4aed-b27b-d2dbd567457a` succeeded at https://coastwise-production.up.railway.app with dataset release `4d0d9de280d8739b08e74322d9cb3bdf78c82d219271c30dd32f8ab047d3faad`. Authenticated live checks verified all ten report aggregates, all 524 defect records, all source PDF hashes, one quotation highlight and rendered page per report, per-report JSON and complete CSV/JSON/Excel/ZIP parity. Unauthenticated API access remained denied. Superseded library/assets and a known old run returned 404/410. Combined local exports were refreshed from the hosted current dataset, including publication warnings. No inference was used for release verification.

Before: separate header, page extraction and later grading requests with extraction-only timing and mixed historical datasets. After: PyMuPDF native text → bounded chunks/context → at most four concurrent calls, one combined extraction/grade request per chunk → local validation/merge → existing report table, PDF evidence and exports. Full execution metrics and limitations: `docs/method-a/latest-release.md`; local execution/cleanup records: `output/coastwise/method-a-brooklyn-one-shot-2026-11-11/`.

### Include existing Feature importance — authorized 2026-11-12

The user explicitly approved including the concurrently restored Feature importance page and data. Keep the one latest saved analysis snapshot, with its original generation date and provenance clearly displayed. This is an additional existing output page; the direct Method A processing path and ten latest Brooklyn runs stay unchanged. Do not recompute analysis, restore older dataset variants or call a model.

- [x] Check the restored navigation, static page/data and provenance offline; distinguish the saved Claude analysis from the newest Brooklyn defect outputs.
- [x] Run current offline tests, Feature importance JavaScript/data consistency checks and current source-package checks.
- [ ] Sync only the approved page/navigation/route/data changes and release records into both repository app folders; push and verify CI.
- [ ] Redeploy the updated package, preserving the same ten-report release data; verify Feature importance, current report/evidence/export APIs and authentication without inference. Record final commits/deployment and cleanup totals.


## Restore feature importance navigation — 2026-11-12

The user explicitly authorizes keeping the original analysis and adding a main-page tab, without an added date label. This is a local restoration only; no inference, push or deployment.

- [x] Restore the original chart JSON and CSV from preserved Git history.
- [x] Re-enable the page and add navigation from the extraction and upload pages.
- [x] Check page/assets/data and the existing latest-only route regression; record the review.

### Review

Restored the original 20-report chart JSON and 25-report score CSV byte-for-byte from preserved shared-repository Git history. Feature importance is accessible through static serving, with links on the main extraction and upload pages and a return link to extraction. No new date label was added. Three latest-workspace tests passed; local HTTP checks returned 200 for the page, JavaScript, stylesheet, JSON and CSV, and confirmed both navigation links. Changes are local only; no model calls, pushes or deployments.
