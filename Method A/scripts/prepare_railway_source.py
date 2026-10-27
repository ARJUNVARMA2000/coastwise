"""Build an allowlisted deployment tree; exclude credentials, reports and outputs."""
import json
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".local" / "railway-source"
if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True)
for name in ["Dockerfile", "pyproject.toml", "uv.lock", ".dockerignore"]:
    shutil.copyfile(root / name, target / name)
shutil.copytree(root / "src", target / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
(target / "scripts").mkdir()
shutil.copyfile(root / "scripts" / "container-entrypoint.sh", target / "scripts" / "container-entrypoint.sh")
files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
assert not any(p.endswith((".pdf", ".env")) or p.startswith(("output/", ".local/", "docs/")) for p in files)
(root / ".local" / "railway-upload-manifest.json").write_text(json.dumps(files, indent=2) + "\n")
print(f"Prepared {len(files)} allowlisted files in {target}")
