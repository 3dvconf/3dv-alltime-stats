from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import read_csv_rows, today_iso, write_csv_rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record(input_path: Path, log_path: Path, provider: str, notes: str) -> None:
    rows = read_csv_rows(input_path)
    provider_key = provider.casefold()
    if provider_key == "openalex":
        matched = sum(bool(row.get("openalex_id")) for row in rows)
    elif provider_key == "semantic scholar":
        matched = sum(bool(row.get("s2_paper_id")) for row in rows)
    elif provider_key == "google scholar":
        matched = sum(
            row.get("google_scholar_citation_count", "") != "" for row in rows
        )
    else:
        raise ValueError(f"Unsupported citation provider: {provider}")
    row = {
        "snapshot_date": today_iso(),
        "provider": provider,
        "corpus_sha256": sha256_file(input_path),
        "paper_count": len(rows),
        "matched_count": matched,
        "notes": notes,
    }
    existing = read_csv_rows(log_path) if log_path.exists() else []
    key = (row["snapshot_date"], row["provider"], row["corpus_sha256"])
    replaced = False
    for index, item in enumerate(existing):
        item_key = (
            item.get("snapshot_date", ""),
            item.get("provider", ""),
            item.get("corpus_sha256", ""),
        )
        if item_key == key:
            existing[index] = row
            replaced = True
            break
    if not replaced:
        existing.append(row)
    write_csv_rows(log_path, existing, list(row))
    print(f"Recorded {provider} snapshot: {matched}/{len(rows)} matched.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append a citation snapshot audit record."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "--provider",
        choices=["OpenAlex", "Semantic Scholar", "Google Scholar"],
        required=True,
    )
    parser.add_argument(
        "--log", type=Path, default=Path("data/curation/citation_snapshot_log.csv")
    )
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    record(args.input, args.log, args.provider, args.notes)


if __name__ == "__main__":
    main()
