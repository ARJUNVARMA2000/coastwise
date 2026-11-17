# Current Method A release

## Publish incognito uploads — authorized 2026-11-17

The user requests: “Push the incognito upload changes to both repos and deploy them to the live website.” This approves publication of the reviewed incognito implementation to personal `ARJUNVARMA2000/coastwise` (`Method A/`) and shared `prksyng54/msds-capstone` (`Method_A/`), followed by the existing personal-main CI-gated Railway deployment. Preserve concurrent changes, the eight-report dataset, authentication, team pages and the persistent volume. No paid inference is authorized for verification.

- [x] Check both current remote heads and CI configuration, Railway source/settings, production health and active work; prepare isolated scoped checkouts.
- [x] Port the reviewed feature, focused tests and incognito documentation to both repository heads without dropping their broader test suites. Review diffs and pass local checks.
- [x] Push both scoped commits to main, verify both CI runs and follow the exact personal application commit through Railway deployment.
- [x] Verify live authentication, private upload/free-preview/session ownership/deletion, UI at desktop/mobile widths and unchanged saved reports/evidence/exports/team pages without paid model calls.
- [x] Record final commits, CI/deployment provenance, live verification and material limitations; stop any owned QA processes.

### Review

Published the incognito implementation to both repositories: personal `bf68244ea057e650640b9a16b6212fad6eba413d` and shared `30e879b109c41780c6179c242f628ef78abf87f3`. Each application commit changes twelve files within its Method A folder; unrelated team folders and the saved release bundle are unchanged. Preserved the repositories' broader CI configuration while adding the eight new privacy/lifecycle tests. Both isolated repository checks passed **59 Python tests** and **21 JavaScript tests**; whitespace and syntax checks passed. The personal repository's concurrent documentation-only commit `1f2c0df9fbf8cb96688241a550cc1cbedd18e264` was fast-forwarded into the publication checkout before adding this review, preserving its README and automatic-deployment guide.

[Personal CI](https://github.com/ARJUNVARMA2000/coastwise/actions/runs/44141911623) and [shared CI](https://github.com/prksyng54/msds-capstone/actions/runs/44141907034) succeeded, including the container builds and saved-site browser smoke checks. Railway's existing Wait for CI connection deployed the exact personal application commit as deployment `e465a726-9482-41b9-a261-a447e847a451`, status SUCCESS. Existing authentication, the single replica, source connection and persistent volume are retained. Live upload URL: https://coastwise-production.up.railway.app/?new=1.

Live Chrome checks at **1440, 390 and 320 px** verified the opt-in checkbox, private upload, free source preview/rendered PDF, Secure/HttpOnly/SameSite=Strict browser-session cookie, ZIP download, denial from a separate authenticated browser, and deletion of both upload and preview. The saved report summary remained byte-for-byte equivalent as JSON before and after every private preview. There were no client errors or horizontal overflow. Paid run requests were explicitly blocked by the browser verifier; completed private inference/findings were covered by the local mocked-inference tests, not another paid production run.

Read-only hosted checks verified the exact updated UI bytes, all unchanged static/team/Feature importance assets, all eight saved reports / **2,597 records**, every source-PDF hash, quotation highlighting/rendered source pages per report, per-report exports and combined CSV/JSON/Excel/ZIP parity. Dataset release remains `ffe4b584ea03992866acaf1bd5eee2596970a2f9c4828d8fd6b3c4066bad6177`. Production had no processing POSTs in the two hours before publication. Verification made **zero paid model calls** and no saved-report reruns.

Private files are temporary until explicit deletion or one-hour expiry; deletion during processing removes access immediately and defers file removal until the worker finishes. Provider retention and ordinary hosting logs are outside this feature's cleanup. Browser contexts are closed; no local QA servers were started for publication. Commits, preflight, CI/deployment provenance, saved-data verification, browser records and screenshots are under `output/coastwise/incognito-release-2026-11-17/`. Completion-record commits touch only task documentation and do not match the application's CI/deployment watch paths.


## Incognito report uploads — 2026-11-17

Request: add an upload option that runs an assessment without saving the report/results in the website. The user approved this plan and local implementation; review and checks are recorded below.

Proposed behavior: an opt-in **Incognito upload** toggle selected before choosing a PDF. Use the existing Method A pipeline and run limits. Show findings, PDF evidence and downloadable exports in a private session view; exclude the upload and results from saved reports, search, shared indexes, combined exports and saved-report replacement/deduplication. Method B upload processing remains a separate integration.

- [x] Inspect upload/run persistence, shared authentication, report discovery, result navigation and evidence/export routes. Current uploads and jobs live under the durable data root; saved-job writes update the shared ledger, and results redirect into the shared report library. Hiding a library row alone would not meet the request.
- [x] Obtain the user's check-in on this concrete plan before implementation. Approved: “Yes, go ahead and implement the incognito upload option.”
- [x] Add ephemeral storage outside the persistent report directory, private run metadata and browser-session ownership independent of the shared workspace login. Enforce ownership for upload/start/status/result/evidence/download/delete requests; never register private uploads in the shared ledger or cloud object storage. Target the current filesystem Railway deployment; reject unsupported storage modes explicitly rather than silently persisting private content.
- [x] Add the opt-in upload toggle, a private result view with the existing findings/evidence/download capabilities, an expiry notice and a **Delete now** action. Keep private results out of the shared library and combined exports, including when the identical PDF already exists as a saved report. Keep owner credentials out of URLs/history and preserve ordinary saved-upload behavior.
- [x] Clean up PDF, text chunks, prompts/responses, exports, caches and private job state on explicit deletion and expiry. Proposed expiry: one hour after results finish (also applies to failed runs); abandoned uploads expire after one hour. Do not delete files under an active worker or interrupt processing on tab closure; use a server-side cleanup task and startup/shutdown cleanup for leftover ephemeral files. Closing a browser tab is not a reliable deletion signal. Preserve only content-free operational/billing metrics if necessary; document their exact fields.
- [x] Add meaningful offline tests with mocked inference: no durable files/index entries, separate-browser access denied, private and saved copies of the same PDF isolated, working evidence/downloads, success/failure/abandonment/deletion/expiry/restart cleanup and unchanged saved uploads. Check the private upload/result flow at desktop/mobile widths without paid model calls.
- [x] Add the implementation review and validation results. Prepare local changes for review; repository pushes/deployment require the user's publication instruction.

### Review

Implemented locally for the current Method A filesystem deployment. `src/coastwise/incognito.py` owns temporary directories, a separate browser-session HttpOnly ownership cookie, in-memory run metadata, one-hour expiry, request/worker leases and per-process locks for crash cleanup. Private uploads and jobs bypass durable job files, the shared ledger, cloud storage and saved-result caches. The extraction view uses a session-authorized private scope for findings, quotations, rendered PDF pages and CSV/JSON/Excel/ZIP downloads; private and saved copies of the same PDF remain isolated. The upload toggle locks after file selection, and the private view shows expiry and Delete now. Deletion also removes previews from the same upload; active worker files are removed on completion rather than while in use.

All **48 Python tests** and **21 JavaScript tests** passed, along with Ruff and both changed JavaScript syntax checks. Eight new privacy/lifecycle tests cover cross-browser denial for upload/start/status/results/evidence/download/deletion, private metrics updates, no durable files/index entries or cached records, identical saved/private PDFs, preview groups, invalid/cancelled uploads, failed run setup/inference, expiry, active-processing deletion and restart cleanup. Chrome verified synthetic upload → findings → PDF evidence → all four downloaded formats → deletion at **1440, 390 and 320 pixels**, with no client errors or horizontal overflow. Evidence is in `.local/incognito-checks/` (browser JSON, screenshots and fixture scripts). Browser inference was mocked; zero paid model calls were made.

The feature retains temporary server files until explicit deletion or expiry (up to 30 seconds for the sweep after access expires), requires the current single-process filesystem host and does not change model-provider retention. No private report content or usage metrics are retained in the durable report index; ordinary hosting request logs and provider billing remain outside this feature's cleanup. The tests include cancellation during upload and a failed file copy to verify no orphaned busy run is left behind.

Application code, tests, README and Railway notes are updated. No Git push or deployment was performed. This workspace has no `.git` metadata; publication remains a separate user instruction. The owned QA server is stopped after verification.

## DSNY Method A 2020–2025 and Coastwise collaboration — 2026-11-16

Scope: execute the six Southwest Brooklyn DSNY reports from 2020 through 2025 using the current high-reasoning Method A configuration and deliver Method A outputs/metrics. The user confirmed all six years, parallel execution, a $30 ceiling and the collaborator invitation. Subsequent correction: “ONLY THINK ABOUT METHOD A FOR NOW. THEY WILL HANDLE METHOD B.” Cross-method comparison and Method B collection are outside this task.

Release update authorized during execution: “REMOVE THE OLD ones as well.” Replace the active Coastwise dataset (Alameda 2017, Berkeley 2017 and DSNY 2016) with only the six new DSNY reports. Preserve source archives, credentials, Feature importance, unrelated team work and Git history. The user was informed of this concrete scope before implementation.

Execution correction: “No deadline run everything.” The initial implementation incorrectly imposed a one-hour report deadline. It fired before the correction could be applied: 17/21 initial chunks were accepted, one 2024 chunk hit the 393,216-token provider limit, and three requests were interrupted with unknown charges retained. No initial chunk was left undispatched. New plan: preserve the first pass; reuse all 17 accepted chunks; subdivide only the four failed primary ranges into 6,000-character chunks, keeping the same high-reasoning model/provider/prompt/schema and bounded adjacent context; process those through one four-request pool with no elapsed-time cutoff; retain the same $30 ledger including original unknown charges; reassemble deterministically and verify the final six-report release. This is failed-range recovery, not a full-cohort rerun or a separate model judge/repair stage.

- [x] Preserve the initial outputs, request bodies and billing ledger; freeze child-to-parent chunk lineage and unchanged high-reasoning settings.
- [x] Run only failed source ranges without an elapsed-time cutoff under the existing $30 ledger; retain successful original chunks without new model calls.
- [x] Rebuild current results/exports from accepted original and recovery chunks, retain first-pass history separately, and verify all source ranges are represented before publication.

- [x] Update the exact release identity allowlist, packager, replacement checks and documentation for DSNY 2020–2025; retain full staging/checksum validation before removing old runtime data.
- [x] Package the six completed saved runs and their source PDFs, with explicit coverage/source-review qualifications. Verify the new dataset and that old result/source URLs return 404 in an isolated local install.
- [x] Prepare the complete current application release; confirm the live service has no active jobs, publish the scoped dataset replacement, and verify exactly six DSNY reports, matching evidence/exports and unavailable old URLs.

### Findings and proposed execution

- The current published showcase contains Alameda 2017, Berkeley 2017 and DSNY 2016. Its DSNY result is not a 2020–2025 comparison cohort.
- The historical inventory identifies all six proposed DSNY PDFs by SHA-256, totaling 290 pages and approximately 21 native-text chunks (2, 2, 2, 3, 6 and 6 by year). These are historical planning counts; verify them after recovering the PDFs. The main source folder currently contains only 2015–2018 DSNY PDFs. Recover exact sources from existing authorized project/team locations or original links; do not silently substitute other editions.
- Reuse the current release's extraction prompt, schema and grading rules: `deepseek/deepseek-v4.1-flash-1017`, pinned `deepinfra/fp8`, high reasoning, temperature 0, 24,000 primary characters per chunk, up to 1,500 adjacent-context characters per side and the same 393,216-token maximum output allowance. Verify current model/provider support and prices before dispatch. This is a new cohort run; historical outputs are context, not automatically equivalent results.
- Run report jobs concurrently and schedule their chunk requests through **one shared global semaphore of four active model requests**. Four is the total across all six reports, not four per report. Use separate report/run directories and one atomic persisted budget ledger, with per-chunk checkpoints and cancellation/stop controls that account for all in-flight work. The existing three-report script processes reports sequentially, so a bounded cross-report scheduler is needed.
- Approved **$30 total new-inference cap**, with a verified preflight reservation estimate. This is a ceiling, not an expected bill. Count billed, reserved and uncertain attempts. Preserve successful chunks; do not automatically retry failures, switch models or add paid judge/repair stages. If rate limits occur, save the failures and prepare a specific recovery proposal.
- Deliver Method A evidence/coverage, defects, measurements, source ratings, supported project grades or uncertainty, runtime, token use and cost. Keep original outputs intact and save review annotations separately. Overall accuracy remains unmeasured. The team handles Method B; do not collect its outputs or make comparison a delivery dependency.
- Prepare the six-report Method A handoff with per-report CSV/JSON/Excel exports and a provenance/coverage summary. Source spot-checks support the qualifications; do not expand this into feature importance or forecasting.
- Target collaborator access to `ARJUNVARMA2000/coastwise`, not the shared repository owned by `prksyng54`. GitHub metadata confirms admin access for the connected user. The connector's collaborator-permission lookup returned 403 (`Resource not accessible by integration`), so existing access is unverified. At implementation, check access/pending invitations through a supported administrative surface and, if needed, invite `prksyng54` with write access. Review the two-page PR's routes, navigation, styling and checks when it exists.
- The user explicitly requests removal of old entries. Replace the active three-report showcase only after the six new saved runs and their release bundle validate. Verify all six reports, evidence links, export counts and removal of superseded result/source URLs. Preserve source archives, Git history and the team's independent pages.

### Checklist

- [x] Inspect the current release, runner, historical DSNY inventory, source availability, common scorer and GitHub repository metadata.
- [x] Record the user's instruction to execute DSNY work in parallel and prepare an unsent teammate reply.
- [x] Obtain the user's plan check-in. Approved 2026-11-16: “Yes, go ahead with DSNY 2020–2025 in parallel using the same high reasoning settings. Keep it under $30 and add prksyng54 as a collaborator on Coastwise.” This supersedes the proposed $3 cap and confirms all six years and the invitation.
- [x] Recover and hash the six PDFs from `.local/three-report-shared-source/report_pdfs/`; all historical SHA-256 hashes, 290 pages and 21 chunks match.
- [x] Freeze requests/settings and implement the bounded cross-report scheduler; 31 focused tests and Ruff passed. Live provider/model/high-reasoning/price checks passed; the conservative full-cohort reservation estimate is $2.55001243 under the approved $30 cap.
- [x] Execute the six reports in parallel under the shared four-request limit and approved total cap; retain raw structured outputs, per-attempt traces, costs, timings, evidence and explicit failed/partial coverage.
- [x] Validate saved Method A outputs and exports, spot-check source/component/measurement ownership and grade support, and package the six reports with a Method A metrics/coverage summary. Method B and cross-method comparison belong to the team.
- [x] Prepare the Method A handoff and publish the authorized six-report replacement. The teammate's two-page PR is independent and must remain untouched.
- [x] Verify collaborator access/invitation for `prksyng54` on `ARJUNVARMA2000/coastwise`. After the user completed GitHub's authentication handoff, the settings page showed “prksyng54 has been added as a collaborator” and “Awaiting prksyng54’s response / Pending Invite.” Invitation exists; acceptance is pending. No duplicate invitation was sent.
- [x] Add the completion review with counts, coverage, measured spend/runtime, comparison limitations, collaborator outcome and any publication/PR links.

### Review

Completed and verified. DSNY 2020–2025, parallel high-reasoning runs, a $30 total cap and the Coastwise collaborator invitation are authorized. The teammate reply remains an unsent draft. This workspace has no `.git` metadata; the planning file is saved locally.

Execution result: all 39 effective native-text chunks are valid across 290 pages after 44 total requests. There are 1443 provisional records and zero numeric project grades. Known billed cost is $0.51975608; unresolved original charges retain $0.36893717 in reservations, totaling $0.88869325 against $30. The initial 17 and subsequently reused 36 accepted chunk files remain byte-identical. The six-report bundle and all CSV/JSON/Excel cells/ZIP checks passed. Local replacement removed the three previous entries; 15 old status/result/source/inspection/page URLs return 404. The team’s merged pages from9213ed0 are preserved unchanged. Publication and live verification are next.

### Completion review — DSNY 2020–2025

Published and verified the six DSNY reports on https://coastwise-production.up.railway.app/extraction. The active Alameda 2017, Berkeley 2017 and DSNY 2016 entries were replaced; all 15 old status/result/PDF/inspection/page endpoints return 404. Source archives and Git history remain.

Processed 290 PDF pages into 1,443 provisional records with 39 accepted current native-text chunks. All project grades remain uncertain, and the source spot-check notes retain omissions, repeated rows, measurement-kind errors and source-rating scope issues. Technical chunk completion is not semantic extraction accuracy.

The first pass made 21 requests (17 accepted, one output-limit rejection and three interrupted by an unrequested one-hour cutoff). The user explicitly removed that deadline. Failed ranges were recovered as 20 smaller chunks (19 accepted, one invalid JSON), then only that failed range was recovered as three 2,000-character children. All 17 original and later 36 reused accepted chunk files remained byte-identical. No judge, model repair, OCR, vision or Method B processing was added.

Total: 44 attempts, $0.51975608 known billed plus $0.36893717 reserved for three unresolved original charges; $0.88869325 committed under the $30 cap. No elapsed-time cutoff applied to recovery. Publication and verification made zero inference calls.

51 application tests passed. Source/PDF/prompt/schema integrity, deterministic assembly, all CSV/JSON/Excel cells and ZIP contents passed. Local and live report/evidence/download checks passed; both application CI runs passed. The team's merged review pages and historical Feature importance assets are unchanged.

The user explicitly approved both main pushes. Personal application commit 291fd6dc585f96baeccc483c3e01848551aedace and shared application commit c98e2a8658356293b092b15fe591375f8cb2e67b are pushed and remote heads verified. Railway deployment a9776ba6-1e49-4c3a-aef6-c5c0388bf0c6 is successful; prior successful deployment cebd08cd-a8fd-4355-8db0-b4310e4161bd is the rollback reference. Release identity: d121c97d59a1fd7f5fb6442bb001302774cca67df4ee99b68ba13a421f3f60b4. All 98 deployment files match the application commit.

`prksyng54` has an existing write-permission invitation to Coastwise, awaiting acceptance. No duplicate invitation was sent. The teammate response remains an unsent draft.

CI: https://github.com/ARJUNVARMA2000/coastwise/actions/runs/43992098317, https://github.com/prksyng54/msds-capstone/actions/runs/43992125263


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
- [x] Sync only the approved page/navigation/route/data changes and release records into both repository app folders; push and verify CI.
- [x] Redeploy the updated package, preserving the same ten-report release data; verify Feature importance, current report/evidence/export APIs and authentication without inference. Record final commits/deployment and cleanup totals.

### Final review — Feature importance and current Method A

Included the one saved Feature importance analysis requested by the user: October 29 Claude results, 25 scored / 20 analyzed reports. The page clearly separates its report-level scores from the latest Brooklyn defect grades. CSV/JSON populations, features, chart references, saved error arithmetic and source/model fields were checked; the browser rendered its charts/table and updated the model-variant control. All 36 current offline tests, relevant Ruff and JavaScript/shell syntax checks passed. No analysis or inference was repeated.

Application commits: personal `6f5aac038d2b3c39379ecdd6c8f3e9f2fba79425` and shared `3f91e5ac597d8998529b7680d5e55beb618821e2`. Both updated CI runs passed (`43159482061` and `43159476463`). Unrelated shared-repo folders were unchanged. Final Railway deployment `0cc028d0-ab28-454e-b0b9-3c0835e8c39c` succeeded at https://coastwise-production.up.railway.app, retaining the same ten-report release ID.

Final authenticated hosted checks verified the Feature importance page, navigation, provenance, matching saved JSON/CSV and script/style assets; all ten Brooklyn report aggregates and source PDF hashes; all 524 defect records; one quotation highlight/rendered source page per report; and per-report/combined exports in CSV, JSON, Excel and ZIP. Authentication remained enforced, while superseded data endpoints and the known old run stayed unavailable. Local preview processes were stopped. `hosted-verification.json` records zero new inference calls for release checks.

Cleanup totals after exact residual audits: 20,403 superseded or duplicate working files / 3,493,974,332 bytes removed. The ten newest Brooklyn outputs/PDFs, current spending ledger, requested latest Feature importance snapshot, code/definitions, credentials and Git history remain. Raw findings/grades and their known limitations are unchanged; the two-minute complete-report target remains unestablished.


## Restore feature importance navigation — 2026-11-12

The user explicitly authorizes keeping the original analysis and adding a main-page tab, without an added date label. This is a local restoration only; no inference, push or deployment.

- [x] Restore the original chart JSON and CSV from preserved Git history.
- [x] Re-enable the page and add navigation from the extraction and upload pages.
- [x] Check page/assets/data and the existing latest-only route regression; record the review.

### Review

Restored the original 20-report chart JSON and 25-report score CSV byte-for-byte from preserved shared-repository Git history. Feature importance is accessible through static serving, with links on the main extraction and upload pages and a return link to extraction. No new date label was added. Three latest-workspace tests passed; local HTTP checks returned 200 for the page, JavaScript, stylesheet, JSON and CSV, and confirmed both navigation links. Changes are local only; no model calls, pushes or deployments.


## Publish four high-reasoning Brooklyn outputs — authorized 2026-11-12

Final scope: ONLY the four saved 2015–2018 high-reasoning outputs and source PDFs. Remove prior versions and the other six Brooklyn reports from current data/repository branches/hosted storage. Keep Feature importance, spending records and Git history. Publication follows the user's explicit instruction after disclosed quality limitations; no model or grading changes.

- [x] Verify both remote heads and existing hosted jobs; logs show no recent scoring/upload requests.
- [x] Package the four saved results in the existing format with exact raw structured outputs, actual elapsed times and limitations.
- [x] Remove superseded ten-report data, old benchmark copies and the six removed source PDFs; preserve spending records and Git history.
- [x] Pass 36offline tests, Ruff, local four-report/evidence/export checks and both repository CI runs.
- [x] Push both repositories and deploy the exact verified 70-file package to the existing Railway service.
- [x] Verify four reports / 162 records, all PDFs/evidence/export formats, authentication, Feature importance, and 404 for all 10 old result/source-download URLs.

### Review

Application commits: personal 7bcf61827d21b5ca8833c726904afdd64237d876; shared 4b742e8f5fcffb1c98d86037262869f5b2340915. Railway deployment 87b6ad6b-e15f-4bed-af1c-49dd61095736 succeeded. Release 18625a8c56a524da872dd8a3a0f973ad338285bf90abce69c521c98764da792e contains only 2015–2018, 162 records, all grades uncertain. Feature importance remains available.

Personal CI 43167071266 passed on the single retry after Chrome startup timed out; the initial tests/build/API checks had passed. Shared CI 43167071006 passed first attempt. No release-code changes or additional inference were needed. All 70 deployed files match the committed application. A proposed temporary SSH key was rejected by automatic approval review; no account credential was registered, local key files were removed, and checks/deployment completed using existing access.

Working-file cleanup explicitly recorded 381 files / 137,813,116bytes, in addition to the runtime installer's replacement of the old ten-run dataset. Raw final structured outputs are byte-identical to the four paid results. Runtime/index/bundle contain four runs. Git history and both prior/current spending ledgers remain. Full verification/cleanup/provenance records are in output/coastwise/method-a-four-report-release-2026-11-12/.

## Replace the four-report website dataset with Alameda, Berkeley and DSNY — 2026-11-16

Status: completed and published. The user approved the plan with “yes”; source processing and the bounded recovery exception are recorded below. The user requests removal of the current four website reports and fresh Method A outputs for three reports only. When asked about editions, the user directed: “CHECK THE AVAILABLE reports and see.” Choose from available sources without another edition question.

### Selected inputs

| Report key | Selected source | PDF pages | Method A chunks |
| --- | --- | ---: | ---: |
| `alameda_2017` | Alameda Piers 1, 2 and 3, SGH summary report, 26 May 2017 | 381 | 20 |
| `berkeley_2017` | Berkeley Municipal Pier, GHD DRAFT Structural Assessment Report, November 2017 | 278 | 15 |
| `dsny_2016` | Southwest Brooklyn Marine Terminal Station Annual Bulkhead Inspection, December 2016 | 44 | 2 |

Alameda is available at `/Users/varma/Scratch/2026-10-17-capstone-human-review/alameda/report-legistar.pdf`, SHA-256 `df565f3438daee56864eac6daaeed276fb2fff9147c22275407217212d3d21d0`. Berkeley was recovered from the [official City of Berkeley PDF](https://berkeleyca.gov/sites/default/files/documents/Berkeley%20Pier%20Structural%20Assessment%20DRAFT.pdf) to `/private/tmp/coastwise-berkeley-2017.pdf`, SHA-256 `b3e4f52a076a88d5afa517072f09c5db0cb38b41a8b0fe9de14522ebfe62fb7f`. Its 13,060,926-byte size matches the former source inventory. DSNY is available at `data/source-reports/new-schema/nyc-dsny/2016-southwest-brooklyn-mts-bulkhead-inspection.pdf`, SHA-256 `d7b576e1177bab20103d995128fd6318299dfe599a1791603b246adeb4a4c552`.

The saved inventory also lists Berkeley's 2019 bird-habitat evaluation; select the 2017 structural assessment to match this defect-extraction showcase. Choose the locally available 2016 DSNY inspection as the representative Brooklyn report. Treat the three entire PDFs as inputs, including appendices. Do not silently trim pages or substitute the already-published DSNY output for the requested new run.

Processing: unchanged native-text Method A one-shot prompt/schema/rules, pinned DeepSeek V4.1 Flash 1017 route, the current saved release's high-reasoning configuration, 24,000 primary characters/chunk, up to 1,500 adjacent-context characters per side, at most four concurrent requests, one extraction-and-provisional-grade call per chunk, no retries or separate model stages. Retain the current release's provider-native output allowance subject to verified provider availability and a persisted **$1 total new-inference ceiling**, including in-flight reservations. Historical billed cost extrapolates to about $0.27 for 37 calls; actual cost and runtime are uncertain, particularly for the long appendices. Verify current provider/model/pricing before dispatch; stop at the cap rather than buying credits, changing models or repeating failed requests. Any terminal/partial output must retain explicit coverage and uncertainty.

Execution adjustment approved 2026-11-16: DeepInfra's shared pool returned HTTP 429 for several Alameda chunks. The user approved “Yes, retry only rejected chunks once.” After the initial batch settles, retry only HTTP-429-rejected chunks once, sequentially after a delay, with identical frozen request bodies/model/provider and the same persisted $1 ceiling. Reuse every successful chunk. Preserve original failures/first results and separate retry traces, and retain any failed second attempt as an explicit coverage gap. This is the only retry exception; no schema repair, successful-chunk repeat or third attempt is authorized.

Operational adjustment: one Berkeley native appendix request is unusually slow. Begin the already-terminal Alameda report's authorized sequential recovery while that single initial request remains in flight. Their maximum two concurrent calls remain below the approved four-call limit; the same atomic ledger retains both full reservations under $1. Preserve first-pass snapshots by report and refresh the final checkpoint from saved run files after both processes finish. No additional request scope is introduced.

- [x] Inspect current release, available source inventory, Method A implementation and deployment path; select and hash all three PDFs and compute native-text chunk counts without inference.
- [x] Obtain the required plan check-in before implementation. User approved with “yes” on 2026-11-16; authorization covers the selected inputs, bounded processing and website replacement.
- [x] Copy the two recovered sources into the project; persist the exact input/settings manifest, first-dispatch checkpoints and a separate bounded usage ledger. Recheck existing processing before deployment.
- [x] Run all three selected PDFs once through Method A; save original structured outputs, per-chunk request/usage/runtime/coverage records, PDF evidence and CSV/JSON/Excel/ZIP exports. Preserve failed/uncertain outcomes without automatic resends.
- [x] Review major findings, source/component ownership, measurements and any published numeric grades against the PDFs; disclose material errors and native-text gaps in the saved results. Keep original model outputs and distinguish review annotations from model findings.
- [x] Generalize the release packager/installer from the hard-coded Brooklyn year set to three explicit report identities, allowing Alameda and Berkeley to share a year; preserve checksum, run/source validation and safe replacement protections. Package exactly these three new results and update dataset labels/documentation.
- [x] Run appropriate offline regression checks and verify all three report views, highlighted PDF evidence, coverage/uncertainty and matching per-report/combined exports in the website. Prepare the complete release before replacing the hosted dataset.
- [x] Publish the scoped release to the existing password-protected Coastwise Railway website; remove the four previous result entries from the active dataset and verify exactly the three selected reports, correct source hashes, working evidence/downloads and unavailable superseded result URLs. Retain the existing Feature importance page, credentials and Git history. Do not expand this into deletion of unrelated historical working files.
- [x] Commit the source-selection lesson with the scoped release and record final report/run counts, measured times, actual billed/held costs, checks, deployment identity and URL.

### Review

Published the full current UI and the three saved Method A outputs to both repositories and the existing protected website. Alameda has 376 records, Berkeley 778 and DSNY 51: 1,205 total across 703 PDF pages, with 35/37 valid chunks. All project grades remain uncertain. Failed extraction pages 121–131 in Alameda and 231–253 in Berkeley, additional native-text gaps and source-review limitations are retained in report details and exports. Measured processing times are 2,480.1 s, 3,581.5 s and 239.7 s respectively; the two-minute target was not met.

Known billed cost is $0.271416901, with $0.245705502 retained for two unknown charges, totaling $0.517122403 committed against the $1 cap. There were 37 initial attempts and 20 approved one-time HTTP-429 recoveries. No successful or interrupted request was repeated, and publication/verification made zero new inference calls. Source-selection and complete-release lessons are committed in both application folders. The final publication review below records commits, CI and live verification.


### Execution checkpoint — bounded appendix interruption

The Berkeley first-pass appendix request (chunk 14, absolute PDF pages 231–253) was interrupted after 1,013.716 seconds without a final response. Its complete worst-case reservation remains held in the shared $1 ledger; it is not eligible for the rate-limit retry exception. All received chunk results were preserved and assembled offline, retaining 170 initial Berkeley records from three valid chunks and explicit gaps. Original interrupted files are retained under `audit/interrupted-first-pass/`. Recover the two reports in separate serial streams, at most two requests in flight globally, each rejected chunk attempted once. Enforce a true 600-second total timeout on the later Berkeley retry stream, retain any unknown charge in the ledger and continue only other approved chunks when their full reservations fit. Never repeat a timed-out or already-retried chunk.

## Publish the full UI and three-report release to both repositories — approved 2026-11-16

The user explicitly instructs: “Deploy everything - the ui CHANGES and the updated 3 reports - to both the repos.” This authorizes the existing local UI changes, both repository pushes and the full website redeployment; no further plan approval is needed. Reuse release `3598c7737174d6496cb77bb2dcbcfc89ea96bef72d08b595569973f7a74a24a1` and its 1,205 saved records. Do not make new model calls.

- [x] Verify the existing UI changes and both GitHub destinations/access: personal `ARJUNVARMA2000/coastwise` under `Method A/`, shared `prksyng54/msds-capstone` under `Method_A/`.
- [x] Sync the complete current UI plus the verified three-report implementation/data into both checkouts, preserving unrelated shared-repository folders and repository history.
- [x] Check the full UI locally with the real three-report data, current Python/JavaScript checks, desktop/mobile views and the matching CI expectations.
- [x] Commit and push both scoped application folders and required CI changes; verify remote heads and both CI runs.
- [x] Redeploy the exact full UI/data package to the existing Coastwise service, retaining its authentication, volume and unchanged dataset release ID; verify live UI assets, exactly three reports / 1,205 records, PDF evidence and all exports.
- [x] Record both commits, checks, final deployment/URL and unchanged model spending; commit the UI-publication lesson.

### Review

Personal application commit `ddd0c34d7454de308c59f4283e50484209e9d543` and shared application commit `2bc358ef73830ee9ee78c9af29d08b30b2bae701` are pushed and remote heads were verified. [Personal CI](https://github.com/ARJUNVARMA2000/coastwise/actions/runs/43930404446) and [shared CI](https://github.com/prksyng54/msds-capstone/actions/runs/43930398438) both passed, including 40 offline tests, JavaScript checks, container build and three-report browser smoke checks. Changes in the shared repository are confined to `Method_A/` and the scoped CI workflow.

Railway deployment `4205278e-ef3c-4003-9959-66d7240a626a` succeeded at [Coastwise](https://coastwise-production.up.railway.app/extraction). All 71 packaged application/container files match the committed application, including the shared stylesheet and complete Method A, upload and Feature importance UI. Authenticated readback verified all nine updated UI assets, exactly three reports / 1,205 records, all source PDF hashes, a quotation highlight/rendered page for each report, and per-report/combined CSV, JSON, Excel and ZIP exports. All eight former result/source URLs return 404. Feature importance JSON/CSV bytes, authentication and credentials are preserved. The live browser visibly shows the new UI and all three report titles with correct counts, without horizontal overflow.

Release ID remains `3598c7737174d6496cb77bb2dcbcfc89ea96bef72d08b595569973f7a74a24a1`; the full UI redeployment reused the verified saved bundle and made no model calls. Billed/held/maximum costs are unchanged. The owned local preview process was stopped. Detailed source, hosted verification, publication and spending receipts remain in `output/coastwise/method-a-three-report-release-2026-11-16/`.


## Restore Alameda and Berkeley alongside DSNY — 2026-11-16

Status: completed and published with the user’s explicit approval.

Requested scope: restore the saved Alameda 2017 (376 records, 381 pages) and Berkeley 2017 (778 records, 278 pages) sections to Method A alongside all six DSNY 2020–2025 reports. Publish eight reports / 2,597 provisional records / 949 PDF pages to the existing Coastwise website and both existing repository main branches. Keep DSNY 2016 excluded. Reuse exact saved native responses, sources and exports, with their partial-coverage warnings. No new inference or model cost; preserve team pages and Feature importance.

- [x] Locate both archived runs and verify all 118 saved-file hashes against the previous published manifest.
- [x] Obtain the required plan check-in before implementation. Approved restoration, both main pushes and publication on 2026-11-16.
- [x] Build the eight-report release from the current six-report bundle plus the two exact saved historical runs; update release allowlist, packager, verification, CI counts and release documentation.
- [x] Verify byte-identical reuse for all eight reports, pass applicable tests, and check the eight reports, PDFs, evidence and all exports locally while DSNY 2016 remains unavailable.
- [x] Sync scoped changes to both repositories, preserve current team work, push verified commits to main and check CI.
- [x] Deploy the exact verified source package to the existing Coastwise service, verify all eight hosted reports and preserved pages/authentication, and record the final review.

### Review

Restored Alameda 2017 (376 records) and Berkeley 2017 (778 records) alongside the six current DSNY 2020–2025 reports: eight reports / 949 pages / 2,597 provisional records. All 327 saved-file hashes and report metadata are byte-identical to their prior published versions. Existing partial-coverage warnings and uncertain project grades are retained; no requests were repeated.

Both application commits are pushed and verified: personal `ed774d9928520355371c83d832e376e01e846be6`, shared `ebc23394b87490f5ccbd8d89f0dda543f620d96d`. [Personal CI](https://github.com/ARJUNVARMA2000/coastwise/actions/runs/43996499403) and [shared CI](https://github.com/prksyng54/msds-capstone/actions/runs/43996574422) passed, including 51 application tests, container builds and browser checks for all eight report titles. The shared change is confined to Method_A and its CI workflow; team pages remain unchanged.

Railway deployment `300deb7b-e861-49aa-9f8a-d1beb8cb6bb8` succeeded. Release `ffe4b584ea03992866acaf1bd5eee2596970a2f9c4828d8fd6b3c4066bad6177` is verified at [Coastwise](https://coastwise-production.up.railway.app/extraction). All 98 deployed files match the committed application source. Authenticated live checks verified all eight reports, exact PDF source hashes, quotation evidence/rendered pages and CSV/JSON/Excel/ZIP exports. All five DSNY 2016 report/source URLs remain 404. Full UI assets, Feature importance, Method B and Answer key bytes are unchanged.

Restoration, deployment and verification used zero model calls and added zero inference cost. The DSNY ledger remains at 44 prior attempts, $0.51975608 billed plus $0.36893717 held, under its $30 cap. The owned local QA server was stopped. Detailed integrity, CI, source, deployment and live verification receipts remain in `output/coastwise/method-a-restored-alameda-berkeley-2026-11-16/`.

## Consolidate Method A defect-type filtering — 2026-11-16

Status: implemented and verified locally. The user confirmed: “Yes, go ahead and implement it.” This section restores the reviewed filter plan after concurrent publication bookkeeping replaced its earlier entry.

- [x] Inspect the screenshot, active filter and all eight saved reports: 2,597 records with 446 distinct raw type strings; DSNY 2024 alone has 187.
- [x] Obtain the required plan check-in before changing application code.
- [x] Add deterministic browsing categories for the approved defect families, with explicit fallback for ambiguous and unreported types. Consolidate spelling, case, separator, singular/plural and location variants; let combined types match each relevant category.
- [x] Show applicable categories alphabetically with per-report counts; preserve the raw descriptions, source records, exports, evidence links, other filters and pagination.
- [x] Check meaningful matching and filter-intersection cases, audit all eight reports, inspect desktop/mobile behavior and real PDF evidence, run applicable existing checks and record the review.

This request covers local implementation and verification. No extraction, grading, model calls, data replacement or publication is included.

### Review

Implemented in `src/coastwise/static/extraction.js`, with a small responsive control-width adjustment in `extraction.css` and six focused regression tests in `tests/extraction_views.test.cjs`. The largest list (DSNY 2024) shrinks from 187 raw labels to 18 categories. Counts for Alameda, Berkeley and DSNY 2020–2025 are respectively 17, 14, 5, 4, 10, 12, 18 and 17 applicable categories. Combined labels can belong to multiple categories, but each filtered table contains a finding once. Ambiguous component-only, rating-only and unspecified labels remain under Other / unspecified; unreported types remain accessible separately. Categories are browsing aids, not changes to engineering assessments.

Validation: all 40 configured Python tests and all 21 JavaScript tests passed, including six new matching/filter/evidence regression cases; JavaScript syntax checks passed. Audited all 446 raw labels across 2,597 records and checked every category count against its matching records. In the real DSNY 2024 browser view, Loose fasteners & connections returns 93 original records; component plus uncertainty filters return seven and an East Top text search returns eight. Pagination and quotation-highlighted PDF evidence (page 98 of 101) work. Desktop (1440 px) and mobile (390 px) have no horizontal overflow; the widened category selector displays the full selected name/count on mobile.

The saved bundle and metadata hashes are unchanged, and all 327 copied saved report/evidence/export files remain byte-identical. The preview used an isolated dataset copy, made no model calls and was stopped after verification. This change is local; neither repository nor the live website was updated. Detailed label/category audit and preview screenshot: `output/coastwise/method-a-defect-filter-2026-11-16/`.

## Publish consolidated Method A defect filters — authorized 2026-11-16

The user explicitly requests: “Push the changes to both repos and deploy them to the live site.” This is the plan check-in and approval for publication of the already-reviewed local implementation. Publish the same scoped filter, responsive styling and regression tests to personal `ARJUNVARMA2000/coastwise` (`Method A/`) and shared `prksyng54/msds-capstone` (`Method_A/`), preserving all other work. The existing personal-main Railway connection waits for CI; use that path and verify its exact deployed commit.

- [x] Inspect both current remote main heads, CI workflow and existing deployment/active jobs; preserve concurrent repository work and the unchanged eight-report release.
- [x] Prepare exact scoped commits for both repos, include the regression tests in CI, run applicable checks and verify the reviewable diffs.
- [x] Push both approved changes to main and verify both CI runs complete successfully.
- [x] Follow the GitHub-triggered Railway deployment to success; verify its commit, authentication, current report data/exports and consolidated live filter behavior.
- [x] Save publication receipts and the live preview, then record final commits, checks, deployment and review. No new inference or report processing.

### Review

Published the approved filter change to personal `ARJUNVARMA2000/coastwise` at `aef03c7bf1327d60a476fc0689e8f2ff21289342` and shared `prksyng54/msds-capstone` at `29c378b7873cb5fc287d21a40dc73df674cf5f90`. Both remote main heads were verified. The change is limited to the Method A filter, its responsive stylesheet, six regression tests, the task record and a CI step that runs all JavaScript view tests; unrelated team work is unchanged.

[Personal CI](https://github.com/ARJUNVARMA2000/coastwise/actions/runs/44002076611) and [shared CI](https://github.com/prksyng54/msds-capstone/actions/runs/44002082957) passed, including 51 Python tests, 21 JavaScript tests, container builds and eight-report browser smoke checks. Local repository validation passed the same 51 Python and 21 JavaScript tests. The earlier local implementation review used the workspace's narrower 40-test Python configuration.

The existing CI-gated GitHub connection deployed personal application commit `aef03c7bf1327d60a476fc0689e8f2ff21289342` successfully as Railway deployment `74b8c940-719c-4876-a795-deeb75906cdd`. Live URL: [Coastwise Method A](https://coastwise-production.up.railway.app/extraction). Both updated filter assets match the approved local bytes exactly. The unchanged dataset release is `ffe4b584ea03992866acaf1bd5eee2596970a2f9c4828d8fd6b3c4066bad6177`.

Authenticated live checks verified all eight reports / 2,597 original records, all source PDF hashes, quotation evidence/rendered pages, matching CSV/JSON/Excel/ZIP exports, 16 UI/data assets, preserved Feature importance and team pages, and 404s for all five excluded DSNY 2016 URLs. Password protection remains enforced. No recent processing POST requests were present before release; no model calls or report processing were started.

The live browser shows 18 DSNY 2024 category options, 93 Loose fasteners & connections records and 39 Missing fasteners records, with original source wording retained. Quotation-highlighted evidence opens at PDF page 15 of 101. Desktop (1440 px) and mobile (390 px) have no horizontal overflow and the full selected category/count fits the mobile control. The browser viewport was reset and the temporary verification tab was closed. Receipts, exact category audit and live screenshot are saved locally under `output/coastwise/method-a-defect-filter-2026-11-16/`.
