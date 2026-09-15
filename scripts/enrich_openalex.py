from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import (
    fetch_json,
    json_dumps,
    merge_semicolon,
    normalize_title,
    read_csv_rows,
    reconstruct_openalex_abstract,
    title_similarity,
    today_iso,
    write_csv_rows,
)

BASE = "https://api.openalex.org/works"


def request_params(api_key: str) -> dict[str, str]:
    return {"api_key": api_key}


def fetch_by_doi(doi: str, api_key: str) -> dict | None:
    if not doi:
        return None
    external_id = f"https://doi.org/{quote(doi, safe='/:;()._-')}"
    url = f"{BASE}/{external_id}?{urlencode(request_params(api_key))}"
    try:
        payload = fetch_json(url)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return payload if isinstance(payload, dict) else None


def fetch_by_title(title: str, year: int, api_key: str) -> tuple[dict | None, float]:
    params = {
        "api_key": api_key,
        "search": title,
        "filter": (
            f"from_publication_date:{year}-01-01,to_publication_date:{year}-12-31"
        ),
        "per-page": "10",
        "select": (
            "id,doi,title,display_name,publication_year,cited_by_count,"
            "counts_by_year,topics,keywords,abstract_inverted_index"
        ),
    }
    payload = fetch_json(f"{BASE}?{urlencode(params)}")
    results = payload.get("results", []) if isinstance(payload, dict) else []
    best, best_score = None, 0.0
    for work in results:
        score = title_similarity(
            title, work.get("display_name") or work.get("title") or ""
        )
        if score > best_score:
            best, best_score = work, score
    if best is not None and best_score >= 0.93:
        return best, best_score
    return None, best_score


def topic_payload(work: dict) -> list[dict]:
    out = []
    for item in work.get("topics") or []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": item.get("id", ""),
                "display_name": item.get("display_name", ""),
                "score": item.get("score", ""),
                "subfield": (item.get("subfield") or {}).get("display_name", ""),
                "field": (item.get("field") or {}).get("display_name", ""),
                "domain": (item.get("domain") or {}).get("display_name", ""),
            }
        )
    return out


def keyword_payload(work: dict) -> list[dict]:
    out = []
    for item in work.get("keywords") or []:
        if isinstance(item, dict):
            out.append(
                {
                    "id": item.get("id", ""),
                    "display_name": item.get("display_name", ""),
                    "score": item.get("score", ""),
                }
            )
    return out


def enrich_row(row: dict[str, str], api_key: str) -> dict[str, str]:
    work = fetch_by_doi(row.get("doi", ""), api_key)
    method = "doi_exact" if work else ""
    confidence = 1.0 if work else 0.0
    if not work:
        work, score = fetch_by_title(
            row.get("paper_title", ""), int(row["publication_year"]), api_key
        )
        if work:
            method = (
                "title_exact"
                if normalize_title(row["paper_title"])
                == normalize_title(work.get("display_name") or work.get("title"))
                else "title_search"
            )
            confidence = score
        else:
            row["openalex_match_method"] = "not_found"
            row["openalex_match_confidence"] = round(score, 4)
            row["openalex_snapshot_date"] = today_iso()
            return row

    row["openalex_id"] = work.get("id", "")
    row["openalex_cited_by_count"] = work.get("cited_by_count", "")
    row["openalex_counts_by_year_json"] = json_dumps(work.get("counts_by_year") or [])
    row["openalex_topics_json"] = json_dumps(topic_payload(work))
    row["openalex_keywords_json"] = json_dumps(keyword_payload(work))
    row["openalex_snapshot_date"] = today_iso()
    row["openalex_match_method"] = method
    row["openalex_match_confidence"] = round(confidence, 4)
    if not row.get("abstract"):
        row["abstract"] = reconstruct_openalex_abstract(
            work.get("abstract_inverted_index")
        )
    row["citation_primary_source"] = "OpenAlex"
    row["citation_primary_count"] = row["openalex_cited_by_count"]
    row["citation_snapshot_date"] = row["openalex_snapshot_date"]
    return row


def enrichment_complete(row: dict[str, str]) -> bool:
    if row.get("openalex_match_method") == "not_found":
        return bool(row.get("openalex_snapshot_date"))
    topics = row.get("openalex_topics_json", "").strip()
    keywords = row.get("openalex_keywords_json", "").strip()
    return bool(
        row.get("openalex_id")
        and row.get("openalex_cited_by_count", "") != ""
        and topics not in {"", "[]"}
        and keywords not in {"", "[]"}
        and row.get("openalex_snapshot_date")
        and row.get("openalex_match_method")
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
    for index, row in enumerate(rows, start=1):
        if (
            force
            and refresh_date
            and row.get("openalex_snapshot_date") == refresh_date
            and not row.get("openalex_match_method", "").startswith("error:")
        ):
            continue
        if not force and enrichment_complete(row):
            continue
        try:
            enrich_row(row, api_key)
        except Exception as exc:
            row["openalex_match_method"] = f"error:{type(exc).__name__}"
            row["manual_notes"] = merge_semicolon(
                row.get("manual_notes", ""),
                [f"OpenAlex enrichment error: {type(exc).__name__}"],
            )
        if index % 25 == 0:
            write_csv_rows(output, rows, fieldnames)
            print(f"OpenAlex checkpoint: {index}/{len(rows)}", flush=True)
        if delay:
            time.sleep(delay)
    write_csv_rows(output, rows, fieldnames)
    matched = sum(bool(r.get("openalex_id")) for r in rows)
    print(f"OpenAlex matched {matched}/{len(rows)} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich 3DV corpus with OpenAlex.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--refresh-date")
    args = parser.parse_args()
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "OPENALEX_API_KEY is required. Create a free OpenAlex key and export it "
            "as an environment variable; do not commit it."
        )
    output = args.output or args.input
    enrich(
        args.input,
        output,
        api_key,
        args.force,
        args.delay,
        args.refresh_date,
    )


if __name__ == "__main__":
    main()
