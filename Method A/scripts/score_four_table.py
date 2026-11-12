"""Score a four-table dataset against the team's answer key, field by field.

    uv run python scripts/score_four_table.py --answer-key .local/answer-key/FourTable_Layout-2026-11-09.xlsx \
        --dataset output/coastwise/four-table-method-a-2026-11-09 --method "Method A" \
        --output output/coastwise/four-table-scoring-2026-11-09/method-a
"""
import argparse
from pathlib import Path

from coastwise import four_table_scoring as scoring

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--answer-key", type=Path, required=True)
parser.add_argument("--dataset", type=Path, required=True, help="Folder with the four-table CSVs")
parser.add_argument("--method", required=True, help="Name shown in every output row, such as Method A")
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()

result = scoring.score(scoring.load_answer_key(args.answer_key), scoring.load_method(args.dataset), args.method)
scoring.write_outputs(result, args.output, args.method, str(args.dataset))
scored = [d for d in result["documents"] if d["status"] == "scored"]
print(f"{args.method}: {len(scored)} documents scored, {len(result['verdicts'])} field verdicts, "
      f"{len(result['errors'])} errors → {args.output}")
for d in result["documents"]:
    print(f'  {d["document"] or d["key_source_file"][:50]:52} {d["status"][:24]:24} '
          f'obs {d.get("observations_matched", "")}/{d["key_observations"]} '
          f'defects {d.get("defects_matched", "")}/{d["key_defects"]}')
for f in result["fields"]:
    print(f'  {f["level"]:12} {f["field"]:18} match {f["match"]:5} partial {f["partial"]:5} different {f["different"]:5} '
          f'missing {f["missing"]:5} not_in_key {f["not_in_key"]:5} rate {f["match_rate"]}')
