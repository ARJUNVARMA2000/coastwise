# Latest Method A release — four reports, 2026-11-12

The selected dataset contains only the new high-reasoning Brooklyn outputs for **2015–2018**, their source PDFs and existing CSV/JSON/Excel/ZIP exports. The previous versions and the six 2020–2025 reports are removed from active data and current repository branches. Git history and spending records remain. Existing Feature importance is retained separately.

## Saved results and actual performance

DeepSeek V4.1 Flash 1017 (`deepseek/deepseek-v4.1-flash-1017`), provider `deepinfra/fp8`, high reasoning, native output allowance 393,216 tokens, strict existing schema, 24,000-character primary chunks, 1,500-character adjacent context, maximum four simultaneous requests. One combined extraction/provisional-grade call per chunk. Eight successful calls, no retry, repair, grader or judge passes. Provider-reported cost **$0.05919185104**. No inference is repeated for publication.

| Report | Pages | Defect records | Calls | Seconds through saved exports |
| --- | ---: | ---: | ---: | ---: |
| 2015 | 40 | 49 | 2 | 232.318 |
| 2016 | 44 | 47 | 2 | 293.133 |
| 2017 | 47 | 51 | 2 | 446.309 |
| 2018 | 25 | 15 | 2 | 275.220 |

The experiment took 1,194.187 seconds (19m54s) from first report start to final saved exports, including the handoff and parallel overlap of 2017/2018. Browser upload/rendering were not measured. **The two-minute target was not met.** All reports contain low-text pages and remain marked partial for source coverage.

## Material limitations

All **162 project grades are uncertain**. Missing or ungraded never implies sound. High reasoning recovered the missing 2018 Western Fender installation/hardware/capacity family, but did not establish uniformly better output. Measurement-kind errors, duplicates, parent-rating scope errors and missing repair/other qualifiers remain. Local material matching rejects some legitimate material/component phrases. The 2018 local merge discarded a distinct small pop-out-spall finding into a corrosion-stains row; 11 of its 14 measurements have incorrect kinds. These outputs are published at the user's explicit request after disclosure.

The direct Method A processing architecture, prompts, rubric, grade rules, UI and new-upload defaults are unchanged. Only saved data selection, release checks, metadata and documentation change. No OCR, vision, multi-agent product orchestration, specialized table processing or retry/repair loop is added. Private model reasoning is excluded from the public package; original local audit logs remain.

## Publication

Dataset release ID: `18625a8c56a524da872dd8a3a0f973ad338285bf90abce69c521c98764da792e`. Four reports, 108 packaged files. The installer verifies checksums and saved-run identities before replacing the runtime dataset and rebuilding its index. The same release remains idempotent.

Targets: personal `ARJUNVARMA2000/coastwise` under `Method A/`, shared `prksyng54/msds-capstone` under `Method_A/`, and the existing Coastwise Railway website. Unrelated shared-repository folders are preserved. Final commit/deployment and verification records will be appended after publication.

## Feature importance

The previously approved saved October 29 Claude analysis remains available: 25 scored / 20 analyzed reports. It is a separate existing analysis, not regenerated from these four defect outputs.
