# Latest Method A release — three reports, 2026-11-16

The approved showcase contains only Alameda Piers 1–3 (May 2017), Berkeley Municipal Pier DRAFT structural assessment (November 2017) and DSNY Southwest Brooklyn annual bulkhead inspection (December 2016). These replace the four former Brooklyn result entries in the active website dataset. Feature importance retains its separately saved October 29 analysis.

## Saved outputs and measured processing

| Report | PDF pages | Records | Valid chunks | HTTP attempts | Processing seconds | Known billed | Reserved/unknown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Alameda Piers 1, 2 and 3 — May 2017 | 381 | 376 | 19/20 | 29 | 2480.1 | $0.124816 | $0.121146 |
| Berkeley Municipal Pier DRAFT structural assessment — November 2017 | 278 | 778 | 14/15 | 26 | 3581.5 | $0.129298 | $0.124559 |
| Southwest Brooklyn DSNY annual bulkhead inspection — December 2016 | 44 | 51 | 2/2 | 2 | 239.7 | $0.017303 | $0.000000 |

There are **1,205 provisional records** across 703 PDF pages. All **1,205 project grades are uncertain**. Thirty-five of 37 chunks produced schema-valid outputs. These are saved machine records, not counts of unique damaged members or certified engineering assessments.

Times include first-pass processing, applicable recovery and saved exports; report-specific timing scopes are retained in metrics. Browser upload-to-render time was not measured. These large reports did not meet the two-minute target.

Model: DeepSeek V4.1 Flash 1017, pinned DeepInfra FP8 route, high reasoning, native 393,216-token output allowance, 24,000-character primary chunks and up to 1,500 adjacent-context characters per side. Initial concurrency was at most four; recovery used one request per report at a time, at most two globally. Prompt, schema and grading-rule files are unchanged. No OCR, vision, separate grader/judge or model repair stage ran.

## Recovery, billing and limits

There were 37 first attempts and 20 explicitly approved single retries of HTTP-429-rejected chunks: **57 HTTP attempts** total. Successful chunks were reused. Two requests did not return final responses and were stopped; neither was repeated after interruption. Provider keep-alives defeated the original idle-read timeout, so the saved runner now also enforces a true 600-second total deadline. An offline regression verifies retained unknown-cost reservations and no automatic resend.

Known settled charges are **$0.271416901**. Two unknown charges retain **$0.245705502** in full worst-case reservations. Total committed maximum is **$0.517122403**, within the approved **$1 cap**. Unknown charges are not reported as zero; their full reservations remain in the ledger. No credit purchase or model/provider substitution occurred.

Remaining failed extraction pages:

- Alameda **121–131**: the authorized recovery request was stopped after 727.186 seconds without a final response.
- Berkeley **231–253**: the initial request was stopped after 1,013.716 seconds without a final response; it was outside the rate-limit retry exception.

Low/no-text and image-only pages remain additional coverage limitations in all three reports. Some appendix text is garbled. Missing findings or grades never imply sound condition.

## Source review and output limitations

Source review retained the main deck, pile, timber-fender/mooring, coating/corrosion and connection/capacity finding families, with report-specific qualifiers. It is not independent gold labeling or a measured accuracy score. Local quote checks match 1,243 of 1,359 shared evidence snippets; exact text presence does not establish semantic ownership or complete extraction.

Repeated mentions, merged pile-ID lists, measurement-kind/association errors, parent-rating scope errors, laboratory observations, repair recommendations and generic criteria can remain in the extracted records. The Berkeley opening-summary response returned no defects despite source findings; later detailed sections retain those principal families. Local material/component checks also reject some legitimate evidence, and useful numeric grading is not demonstrated. Review notes and original PDFs remain available with CSV/JSON/Excel/ZIP exports. Raw first outputs and private provider audits are preserved locally; private reasoning and credentials are excluded from the website bundle.

Alameda's report is dated May 2017, but its above-water inspection describes July–December 2016 field work. The website labels the selected edition as a report year, without inventing an inspection date.

## Publication and verification

Release ID: `3598c7737174d6496cb77bb2dcbcfc89ea96bef72d08b595569973f7a74a24a1`. The checksum manifest contains 146 packaged files. The installer checks exactly three report identities, allowing Alameda and Berkeley to share 2017, before replacing only the configured runtime dataset. Repeated installation is idempotent. Git history, credentials and unrelated local UI work are preserved.

Forty offline checks pass, including same-year selection, rejection of invalid/duplicate release identities, safe replacement/idempotence and timeout reservation/no-resend behavior. A local three-report snapshot verified source PDFs, quotation highlights, per-report and combined CSV/JSON/Excel/ZIP parity, unavailable superseded URLs and unchanged Feature importance data. Final source-package and hosted verification are recorded below when complete.

Execution records: `output/coastwise/method-a-three-report-release-2026-11-16/`. The source-selection convention is committed locally as `ddd7958`.
