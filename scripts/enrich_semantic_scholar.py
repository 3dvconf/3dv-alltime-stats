from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import (
    fetch_json,
    json_dumps,
    normalize_doi,
    read_csv_rows,
    today_iso,
    write_csv_rows,
)

BASE = "https://api.semanticscholar.org/graph/v1/paper/batch"
FIELDS = (
    "paperId,title,citationCount,influentialCitationCount,fieldsOfStudy,externalIds"
)
BATCH_SIZE = 400
S2_OUTPUT_FIELDS = [
    "s2_paper_id",
    "s2_citation_count",
    "s2_influential_citation_count",
    "s2_fields_of_study_json",
    "s2_snapshot_date",
    "s2_match_status",
]


def semantic_id(row: dict[str, str]) -> str:
    doi = normalize_doi(row.get("doi", ""))
    if doi:
        return f"DOI:{doi}"
    if row.get("dblp_key"):
        return f"DBLP:{row['dblp_key']}"
    return ""


def chunks(values: list, size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def enrichment_complete(row: dict[str, str]) -> bool:
    if row.get("s2_match_status") == "not_found":
        return bool(row.get("s2_snapshot_date"))
    return bool(
        row.get("s2_paper_id")
        and row.get("s2_citation_count", "") != ""
        and row.get("s2_snapshot_date")
        and row.get("s2_match_status")
    )


def enrich(
    path: Path,
    output: Path,
    api_key: str,
    force: bool,
    delay: float,
    refresh_date: str | None = None,
) -> None:
    rows = read_csv_rows(path)
    fieldnames = list(rows[0].keys()) if rows else []
    if rows and "s2_match_status" not in fieldnames:
        snapshot_index = fieldnames.index("s2_snapshot_date")
        fieldnames.insert(snapshot_index + 1, "s2_match_status")
    pending = [
        (index, row, semantic_id(row))
        for index, row in enumerate(rows)
        if semantic_id(row)
        and not (
            force
            and refresh_date
            and row.get("s2_snapshot_date") == refresh_date
            and not row.get("s2_match_status", "").startswith("error:")
        )
        and (force or not enrichment_complete(row))
    ]
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    batches = list(chunks(pending, BATCH_SIZE))
    for batch_no, batch in enumerate(batches, start=1):
        ids = [item[2] for item in batch]
        try:
            payload = fetch_json(
                f"{BASE}?{urlencode({'fields': FIELDS})}",
                headers=headers,
                data=json.dumps({"ids": ids}).encode("utf-8"),
                method="POST",
            )
        except Exception as exc:
            snapshot_date = today_iso()
            for row_index, row, _ in batch:
                row["s2_snapshot_date"] = snapshot_date
                row["s2_match_status"] = f"error:{type(exc).__name__}"
                rows[row_index] = row
            write_csv_rows(output, rows, fieldnames)
            print(
                f"Semantic Scholar batch {batch_no} failed with "
                f"{type(exc).__name__}; marked {len(batch)} rows for review.",
                flush=True,
            )
            if delay and batch_no < len(batches):
                time.sleep(delay)
            continue
        if not isinstance(payload, list) or len(payload) != len(batch):
            received = len(payload) if isinstance(payload, list) else "non-list"
            raise ValueError(
                f"Semantic Scholar batch response length {received} does not match "
                f"request length {len(batch)}"
            )
        for (row_index, row, identifier), paper in zip(batch, payload):
            for field in S2_OUTPUT_FIELDS:
                row[field] = ""
            row["s2_snapshot_date"] = today_iso()
            if not paper:
                row["s2_match_status"] = "not_found"
                rows[row_index] = row
                continue
            row["s2_paper_id"] = paper.get("paperId", "")
            citation_count = paper.get("citationCount")
            influential_count = paper.get("influentialCitationCount")
            row["s2_citation_count"] = (
                str(citation_count) if citation_count is not None else ""
            )
            row["s2_influential_citation_count"] = (
                str(influential_count) if influential_count is not None else ""
            )
            row["s2_fields_of_study_json"] = json_dumps(
                paper.get("fieldsOfStudy") or []
            )
            row["s2_match_status"] = (
                "doi_exact" if identifier.startswith("DOI:") else "dblp_exact"
            )
            rows[row_index] = row
        write_csv_rows(output, rows, fieldnames)
        print(f"Semantic Scholar checkpoint: batch {batch_no}", flush=True)
        if delay and batch_no < len(batches):
            time.sleep(delay)
    matched = sum(bool(r.get("s2_paper_id")) for r in rows)
    print(f"Semantic Scholar matched {matched}/{len(rows)} rows")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optionally audit 3DV citations with Semantic Scholar."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to pause between batch requests (default: 2.0).",
    )
    parser.add_argument("--refresh-date")
    args = parser.parse_args()
    output = args.output or args.input
    enrich(
        args.input,
        output,
        os.environ.get("S2_API_KEY", "").strip(),
        args.force,
        args.delay,
        args.refresh_date,
    )


if __name__ == "__main__":
    main()
