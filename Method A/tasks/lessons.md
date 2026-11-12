# Project lessons

## 2026-11-11 — Publish only the requested current dataset

When the user asks to remove superseded data everywhere, clarify whether “latest” means one current run per report and whether Git history should remain. Here the user confirmed the newest Method A output and source PDF for all ten Brooklyn reports, deletion from working files/current repository branches/hosted storage, and preservation of Git history. Remove old datasets rather than retaining hidden replay bundles. Push both the personal Coastwise repo and the shared repo's Method_A folder when requested. The user explicitly chose deployment of the latest outputs despite the quality gate, so keep the confirmed limitations visible rather than silently repairing or relabeling results.

Protect schema definitions by exact role/path, not by the substring `schema`: saved `new_schema.json` and `new_schema.csv` are extracted datasets and must follow the data deletion scope. Finish with a bounded residual audit, including duplicate intake uploads and stale batch indexes. Validate the kept source hashes and current terminal run IDs before deletion.

When concurrent work restores a separate feature, leave it untouched until scope is clear. The user explicitly requested including the existing Feature importance page. Retain its latest saved snapshot and label its original date/model and population so readers do not mistake its historical report-level scores for the latest one-shot defect grades. Do not recompute it or expand the extraction architecture.

## 2026-11-11 — Implement the simplified path without reopening old work

When the user says “Implement and forget about existing work,” proceed with the authorized offline implementation. Leave historical cohort processing, outputs and spending records untouched; do not make another preservation audit, completion of an old batch or paid evaluation a prerequisite. Keep Method A to native text, bounded concurrent chunks, one extraction-and-grade call per chunk, local checks and the existing UI/exports.

## 2026-10-26 — Full New_Schema means every field, with LLM judging

The user corrects the partial comparison: a rubric-only assessment does not satisfy a request to assess every New_Schema field. Do not leave fields labeled “not assessed” merely because they were omitted from the AI contract. Extract all factual fields with evidence and explicit missing-value reasons, keep reviewer metadata truthful, then judge semantic agreement separately from generation. The user explicitly requests an LLM judge and a cheaper open model (DeepSeek); preserve prior Sonnet work as history rather than treating it as the full-schema result.

## 2026-10-25 — Check local report deliveries before remote access work

The user points to `source_reports` as the available PDF source; the actual path is `data/source-reports/new-schema`, with 29 PDFs and a row-keyed manifest. Check the project root and local source directories before spending time on Drive download permissions. Newly supplied local PDFs take precedence as the concrete batch inputs; preserve their filenames and source identity in the saved table. The manifest also records one human-table row with no source link.

## 2026-10-25 — Honor the confirmed bulk input scope

The user explicitly chose to score every PDF in the shared project folder, including course guides, rubric/background documents and sample capstone reports, after being offered the alternative of linked inspection reports. Do not replace that confirmed scope with a more domain-specific dataset. Include every accessible folder PDF and retain missing-evidence/applicability findings in the saved table.

## 2026-10-25 — Anchor the baseline to Arjun's explicit assignment

The user clarified the assignment: run one-shot Claude on several reports by extracting native text, chunking it, prompting with the agreed rubric, and logging extraction performance, tokens, cost, and runtime. This experiment is the baseline.

Keep rubric-based scoring distinct from source-fact extraction. The current Method A engine supports native-text chunks and one extraction per chunk, with usage/cost/runtime logs, but its default prompt explicitly forbids assigning new condition scores. A deployed website and editable prompt do not prove that rubric scoring, quality evaluation, or the baseline experiment have been completed. State the remaining rubric/output-contract integration and live evaluation explicitly before claiming scored results.

The website and Method B are reusable supporting work. Prioritize the agreed Claude baseline before further interface or method expansion. Weekly report wording must distinguish completed infrastructure from completed experiments; hypothetical scored results should be labeled as an assumption.

The updated team's assessment script and full rubric are now recovered and versioned under `docs/method-a` and `src/coastwise/baseline_assets`. Use that format for Arjun's requested scoring experiment. Do not present the separate provisional three-table extractor as the completed rubric baseline. Retain raw one-shot outputs and flag rubric inconsistencies; do not repair them silently or hide them by voting.

## 2026-10-25 — Verify effective platform settings

Railway CLI acceptance and schema validation of `railway.json` did not mean a newly created service used its settings. The live settings state that new services cannot opt into that legacy format after August 28, 2026. Use the supported service API and check effective health, resource and storage configuration after deployment. The nonsecret setup manifest is `deploy/railway-service.json`; do not restore the ignored root configuration file.


## 2026-10-26 — Stop limits must account for in-flight jobs

When the user sets a report-count stop during a concurrent batch, count reused completed results as well as newly completed ones and freeze dispatch immediately. If the limit is already reached, terminate local in-flight processing promptly, retain all completed artifacts, and record uncertain billing for interrupted requests. An in-flight completion can race with shutdown; disclose it and keep it outside the requested limited collection rather than hiding or deleting it. Do not automatically resume a held batch.


## 2026-10-26 — Evaluate prompt revisions with a common judge

When changing extraction and judge instructions together, retain original outputs and judge results, then judge both extraction versions with the same revised judge and the same native source context. Compare the intersection of comparable fields to avoid denominator changes masquerading as gains. Separate better rule consistency from semantic accuracy; prompt revisions can still regress citations, missing-value serialization or unsupported inferences. Use source-hash allowlists and a separate experiment root to enforce an exact same-report rerun without resuming a held bulk queue.

## 2026-10-26 — Organize the website around reports people recognize

The user finds the saved reports and results unintuitive. A ledger of execution records and schema-named tabs is insufficient for browsing completed work. Make report identity, assessment version, key findings, human disagreements and source evidence the visible hierarchy; keep upload configuration separate from reading saved results. Distinguish unique PDFs from repeated runs/previews and label original/revised outputs directly. At narrow widths, do not hide human comparison behind a wide technical table or a full screen of run settings.

Show deterioration scores as severity with an explicit 1 good → 4 serious/critical legend; “4 / 4” looks like a positive performance rating. A completed or schema-valid assessment is not verified scoring accuracy. Human raw condition descriptions and automated factual-agreement verdicts must remain distinct from reviewed numerical rubric scores.


## 2026-10-26 — Keep the active report library focused on current results

The user asks to remove older report versions and original-prompt details after completing the labeled set. Prefer one current, complete assessment per report in the normal browsing flow, with direct human comparison and evidence. Archive superseded entries reversibly and retain experimental files; do not keep version selectors, previous-answer columns or original/revised prompt labels visible merely because historical data exists. Preserve useful sources that have no verified replacement.

## 2026-10-26 — Match comparison emphasis to the presentation context

The user corrects the client-facing hierarchy: the progress update should lead with completed report processing, model outputs, findings and evidence. Repeating discrepancy counts in the library, navigator and default report view overemphasizes shortcomings. Keep truthful human comparisons, quality qualifications and review tools accessible, but use quieter styling, an intentional comparison tab and all-fields default order rather than leading with discrepancy heatmaps and expanded warnings. Do not substitute completion metrics for accuracy claims or change underlying outputs to improve the presentation.

## 2026-10-26 — “LLM version” means the actual extracted output on entry

The user clarifies that a presentation-friendly report page should open directly on the LLM's extracted field values, with model justifications and source evidence. An Overview showing the severity rationale does not satisfy that request by itself. Use Model output as the first/default view for full-schema reports, without human answer columns or comparison verdicts on that opening screen. Keep comparison as an explicit separate view unless the user requests its removal.

## 2026-10-27 — Scope transcript-driven plans to the requested app changes

When the user asks for an implementation plan for Coastwise and clarifies that human labeling is separate, inspect the actual app and specify schema, extraction-prompt, runner, UI and export changes. Treat human annotation and adjudication as independent work, not delivery gates or app features. Preserve the separate comparison workflow, but let new defect extraction complete without human references or automatic judge calls. Focus the first release on evidence-linked defects, measurements and section assessments; sequence cross-report matching and trends after that foundation.


## 2026-10-27 — Exercise the real structured-output contract before declaring extraction ready

The expanded defect contract passed fixture tests but live DeepSeek responses exposed output padding and entity/ID confusion. Keep incomplete/invalid outputs rejected, retain billed traces, and inspect the actual response before raising output limits. Compact JSON, short distinct IDs, explicit physical-entity boundaries and a concise relationship example resolved the integration smoke test. Schema descriptions should explain table membership as well as field types. Keep human labels outside extraction, bound each verification request and the total spend, and distinguish quote presence/JSON validity from semantic engineering accuracy.


## 2026-10-28 — Preserve physical section scope across reports

Use source-established physical geometry and same-PDF numbering definitions to retain canonical structure/section IDs across repeated reports. Similar directions/names, old/new construction and numbered/lettered schemes do not establish a crosswalk. Keep original names/local IDs and explicit unresolved scope. Catalog presence or zero extracted findings never means inspected absence or sound condition. Preserve raw model responses and distinguish source-only normalization/review from human gold labeling or measured accuracy. Keep findings report-specific and defer trends until explicitly requested. A source quote that matches native text can still be paired with the wrong description; flag that limitation and retain provenance.


## 2026-10-27 — Preserve source-established section identity across reports

Repeated reports of the same structure must retain a source-backed section catalog and stable section IDs. Match physical geometry/scope, not a shared direction or similar name; old/new construction and numbered/lettered schemes can differ. Preserve original names, local IDs and mapping evidence. Retain source-named sections with unclear coverage when findings are absent; do not infer inspected absence, soundness or a persistent defect identity. Keep dated findings and human labeling separate. Source-only review may correct explicit parent-link or observation-kind errors with preserved provenance; it is not human gold labeling or trend analysis.

## 2026-11-09 — Carry scoring through a change in navigation

When Coastwise moves from report-first review to the four-table structure register, explicitly carry the requested grading into the new tables and details. Confirm the scale and what is being graded at each level. Keep defect damage grade, structure condition by inspection, reported ratings, evidence, and coverage distinct. Verify that both numeric grades are visible on real saved examples before handing off the preview.

## 2026-11-09 — Preserve reasoning when projecting saved assessments

When a Coastwise view projects a saved inventory into the four-table register, audit the fields left behind before recommending new inference. Verify that a defect detail keeps its model rationale, rubric criterion, extent, confidence, alternatives, evidence, and source-stated action when available. Keep the original report-wide narrative and rationale accessible at inspection scope, and distinguish a scoped row count from the full inspection's findings.

## 2026-11-10 — Keep the transcript plan within Arjun's Method A scope

The user selects Method A and all available Southwest Brooklyn report versions as the pilot. Prioritize one auditable assessment per defect, retaining the original rating and applicable scale alongside the project's 1–4 damage grade, rationale, measured values, exact quotes and source links. Feature importance and forecasting are other team members' responsibilities. Capture source-stated material and role context for the handoff, and treat ASTM enrichment and structure-level aggregation as separately defined follow-on work. The user authorizes up to $15 total through OpenRouter and requests a plan check-in before implementation. Use the current defect damage mapping explicitly, and keep parent condition ratings at their reported scope.

## 2026-11-10 — Retire tuned reports to regression

Once a report informs a prompt, parser or grading change, keep the whole report in regression and reserve a different unseen report for the next evaluation. Freeze the pipeline and predeclare source checks before evaluation. Report citation fidelity, defect ownership, explicit rubric rule application and unsupported grades with separate denominators. Overall grading accuracy requires separate reference data and remains unmeasured here.

## 2026-11-11 — Repair evaluated failures offline, then use different fresh inputs

A failed independent source becomes regression material, including the whole report and related versions. Preserve its first raw responses and results. Repair ordinary source binding and rules offline, record saved-response replay separately from new model extraction, and refreeze before another independent selection. Obtain approval on the new concrete paid plan; prior approval does not authorize another validation run or the Brooklyn cohort. The original $15 budget persists across chats.


## 2026-11-11 — Finish Method A with practical acceptance and a fixed endpoint

The user explicitly stops expansion and repeated edge-case repair/validation. Keep a4abad3 frozen, use eligible fresh sources already identified, and propose only one small final pass before paid approval. Success means evidence-supported numeric grades, retained major findings/component/location, clearly flagged uncertainty and working tables/UI/exports. Minor documented omissions are acceptable; every legacy assertion need not pass. After that pass, issue go/no-go and, if usable, seek cohort-plan approval, review the Brooklyn outputs and deploy. Do not begin another cycle unless a major correctness issue would make the outputs misleading.


## 2026-11-12 — Preserve the original feature importance page

The user explicitly keeps the original feature importance analysis as an exception to the latest-only cleanup, and wants a main-page navigation tab without an added date label. Preserve its chart JSON and CSV along with its UI when preparing future releases.
