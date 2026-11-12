# Method A: one-shot defect extraction

The default upload workflow is native PDF text → bounded chunks → one DeepSeek call per chunk
for findings and provisional grades → local validation and merge → the existing defect table,
PDF evidence viewer and CSV/JSON/Excel/ZIP exports. The linked tables are an output adapter.

`src/coastwise/method_a_one_shot.py` contains the runner, compact schema and local projection.
The response stores shared component context and short source quotations once, referenced by ID.
Each defect retains component/location, finding, original measurements and units, original rating
and scale, project grade or uncertainty, a short reason and absolute PDF source pages.

Default settings are DeepSeek `deepseek/deepseek-v4.1-flash-1017`, 24,000 native characters per
chunk, at most 1,500 characters of adjacent context on either side, four concurrent requests,
12,000 output tokens per request, at most 100 requests, a $2 per-run cap and a 600-second runtime
limit. Provider retries are disabled. There are no header, grading, judging or repair model passes.
Request logs, raw responses, prompts, schema, native text and per-chunk checkpoints remain local
to the new run. A saved checkpoint is never silently reprocessed.

Numeric grades require a registered rule plus verified native-text evidence for its component,
material, required measurements/units and threshold. Unsupported or ambiguous proposals become
uncertain without another model call. Original ratings remain separate. Missing, ungraded or
zero extracted findings never means sound condition. Rule checks are conservative; literal quote
matching does not establish full extraction recall or semantic correctness.

The home page opens Method A results, and Upload opens the direct workflow. The current workspace
and aggregate downloads contain only new one-shot uploads. Historical code and saved data remain
on disk outside this experience. Normal startup does not resume the historical bulk queue.

Scans, photographs, drawn marks, image-only rating scales and unreliable native reading order
are limitations. No OCR, image interpretation, form reading or special table pipeline is added.
Failed chunks remain explicit partial results, with usable saved rows and no automatic retry.
Selected-page results also retain their limited scope.

## Timing and offline validation

The browser measures upload-start through the rendered graded table and ready downloads. It also
shows processing-start through table readiness, request count and cost (or unknown cost). Upload
elapsed time includes any user wait before pressing Extract. The server records extraction/local
validation/export time separately. Only a complete full-report table can satisfy the completion
measurement; previews and partial/selected-page tables do not count.

Approximately two minutes for a typical 20–50-page native-text report is an **unmeasured target**.
Larger/dense reports may take longer. Before a paid benchmark, present one existing representative
full report, exact settings, estimated cost and hard cap for approval; run it once after approval.
The offline fixture/browser checks do not measure live DeepSeek latency or extraction accuracy.

Bounded offline checks:

```sh
uv run --no-sync pytest -q tests/test_method_a_one_shot.py tests/test_method_a_one_shot_web.py \
  tests/test_extraction_web.py tests/test_scoring_web.py tests/test_web.py tests/test_method_a_portable.py
```

Before: a header request, page extraction requests and separate grading requests, with extraction
timing excluding later stages. After: one combined request per size-bounded chunk, local evidence
and rule checks, concise records, and timing through the usable table and downloads.
