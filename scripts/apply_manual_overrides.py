from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import read_csv_rows, write_csv_rows


def apply_overrides(input_path: Path, overrides_path: Path, output_path: Path) -> None:
    rows = read_csv_rows(input_path)
    fieldnames = list(rows[0].keys()) if rows else []
    by_id = {row.get("record_id", ""): row for row in rows}
    overrides = read_csv_rows(overrides_path) if overrides_path.exists() else []
    applied = 0
    for override in overrides:
        record_id = override.get("record_id", "")
        field = override.get("field", "")
        if not record_id or not field or record_id not in by_id:
            continue
        if field not in fieldnames:
            raise ValueError(f"Override references unknown field: {field}")
        by_id[record_id][field] = override.get("new_value", "")
        note = f"override:{field}:{override.get('reason', '')}".rstrip(":")
        existing = by_id[record_id].get("manual_notes", "")
        by_id[record_id]["manual_notes"] = f"{existing}; {note}" if existing else note
        applied += 1
    write_csv_rows(output_path, rows, fieldnames)
    print(f"Applied {applied} manual overrides from {overrides_path}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reapply curated corrections after a reproducible rebuild."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--overrides", type=Path, default=Path("data/curation/manual_overrides.csv")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    apply_overrides(args.input, args.overrides, args.output or args.input)


if __name__ == "__main__":
    main()
