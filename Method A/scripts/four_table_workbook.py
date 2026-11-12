"""Write a Method A dataset folder's CSVs to a single Excel workbook."""
import sys
from pathlib import Path

from coastwise.four_table import COLUMNS
from coastwise.method_a_exports import workbook


def main(folder: Path) -> None:
    sheets = {name: (folder / f"{name}.csv").read_text() for name in COLUMNS}
    target = folder / "four-table.xlsx"
    target.write_bytes(workbook(sheets))
    print(target)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
