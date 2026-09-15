from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from build_3dv_corpus import EditionRegistry, load_edition_registry
from core import (
    is_admin_record,
    load_json_cell,
    normalize_doi,
    normalize_title,
    read_csv_rows,
    write_csv_rows,
)

METADATA_REVIEW_FIELDS = [
    "record_id",
    "publication_year",
    "paper_title",
    "authors",
    "doi",
    "dblp_url",
    "publisher_url",
    "openalex_match_method",
    "s2_match_status",
    "s2_paper_id",
    "s2_citation_count",
    "s2_snapshot_date",
    "review_reasons",
    "source_keywords",
    "abstract",
    "fallback_citation_source",
    "fallback_citation_count",
    "fallback_citation_snapshot_date",
    "review_status",
    "manual_notes",
]


def metadata_review_reasons(row: dict[str, str]) -> list[str]:
    reasons: list[str] = []
    match_method = row.get("openalex_match_method", "")
    if match_method == "not_found":
        reasons.append("openalex_not_found")
    elif match_method.startswith("error:"):
        reasons.append("openalex_error")
    elif not row.get("openalex_id"):
        reasons.append("missing_openalex_match")
    if row.get("openalex_cited_by_count", "") == "":
        reasons.append("missing_openalex_citation")
    if not load_json_cell(row.get("openalex_topics_json", "")):
        reasons.append("missing_openalex_topics")
    if not load_json_cell(row.get("openalex_keywords_json", "")):
        reasons.append("missing_openalex_keywords")
    s2_status = row.get("s2_match_status", "")
    s2_attempted = bool(s2_status or row.get("s2_snapshot_date"))
    if s2_status == "not_found":
        reasons.append("semantic_scholar_not_found")
    elif s2_status.startswith("error:"):
        reasons.append("semantic_scholar_error")
    elif s2_attempted and not row.get("s2_paper_id"):
        reasons.append("missing_semantic_scholar_match")
    elif s2_attempted and row.get("s2_citation_count", "") == "":
        reasons.append("missing_semantic_scholar_citation")
    return reasons


def metadata_review_row(row: dict[str, str]) -> dict[str, str]:
    fallback_source = row.get("citation_primary_source", "")
    fallback_count = row.get("citation_primary_count", "")
    fallback_recorded = (
        bool(fallback_source)
        and fallback_source != "OpenAlex"
        and fallback_count != ""
        and bool(row.get("manual_notes"))
    )
    return {
        "record_id": row.get("record_id", ""),
        "publication_year": row.get("publication_year", ""),
        "paper_title": row.get("paper_title", ""),
        "authors": row.get("authors", ""),
        "doi": row.get("doi", ""),
        "dblp_url": row.get("dblp_url", ""),
        "publisher_url": row.get("publisher_url", ""),
        "openalex_match_method": row.get("openalex_match_method", ""),
        "s2_match_status": row.get("s2_match_status", ""),
        "s2_paper_id": row.get("s2_paper_id", ""),
        "s2_citation_count": row.get("s2_citation_count", ""),
        "s2_snapshot_date": row.get("s2_snapshot_date", ""),
        "review_reasons": "; ".join(metadata_review_reasons(row)),
        "source_keywords": row.get("source_keywords", ""),
        "abstract": row.get("abstract", ""),
        "fallback_citation_source": fallback_source
        if fallback_source != "OpenAlex"
        else "",
        "fallback_citation_count": fallback_count
        if fallback_source != "OpenAlex"
        else "",
        "fallback_citation_snapshot_date": (
            row.get("citation_snapshot_date", "")
            if fallback_source != "OpenAlex"
            else ""
        ),
        "review_status": (
            "manual_fallback_recorded" if fallback_recorded else "manual_review_needed"
        ),
        "manual_notes": row.get("manual_notes", ""),
    }


def refresh_coverage_report(
    output_dir: Path,
    rows: list[dict[str, str]],
    registry: EditionRegistry,
) -> None:
    path = output_dir / "coverage_report.csv"
    existing = read_csv_rows(path) if path.exists() else []
    by_year = {int(row["year"]): row for row in existing if row.get("year")}
    report: list[dict[str, object]] = []
    for year in sorted(set(registry.held_editions) | registry.no_edition_years):
        if year in registry.no_edition_years:
            report.append(
                by_year.get(year)
                or {
                    "year": year,
                    "edition_status": "no edition",
                    "canonical_source_available": "n/a",
                    "supplemental_snapshot_available": "n/a",
                    "supplemental_raw_record_count": "",
                    "live_paper_rows": 0,
                    "openalex_citation_rows": 0,
                    "semantic_scholar_citation_rows": 0,
                    "topic_classified_rows": 0,
                    "build_status": "not_applicable",
                }
            )
            continue
        item = dict(by_year.get(year, {}))
        subset = [row for row in rows if int(row.get("publication_year") or 0) == year]
        complete = (
            bool(subset)
            and all(row.get("citation_primary_count", "") != "" for row in subset)
            and all(bool(row.get("primary_topic")) for row in subset)
        )
        item.update(
            {
                "year": year,
                "edition_status": item.get("edition_status", "held"),
                "canonical_source_available": item.get(
                    "canonical_source_available", "yes"
                ),
                "supplemental_snapshot_available": item.get(
                    "supplemental_snapshot_available", ""
                ),
                "supplemental_raw_record_count": item.get(
                    "supplemental_raw_record_count", ""
                ),
                "live_paper_rows": len(subset),
                "openalex_citation_rows": sum(
                    row.get("openalex_cited_by_count", "") != "" for row in subset
                ),
                "semantic_scholar_citation_rows": sum(
                    row.get("s2_citation_count", "") != "" for row in subset
                ),
                "topic_classified_rows": sum(
                    bool(row.get("primary_topic")) for row in subset
                ),
                "build_status": "complete" if complete else "needs_review",
            }
        )
        report.append(item)
    write_csv_rows(
        path,
        report,
        [
            "year",
            "edition_status",
            "canonical_source_available",
            "supplemental_snapshot_available",
            "supplemental_raw_record_count",
            "live_paper_rows",
            "openalex_citation_rows",
            "semantic_scholar_citation_rows",
            "topic_classified_rows",
            "build_status",
        ],
    )


def validate(
    input_path: Path,
    output_dir: Path,
    strict: bool,
    manifest_path: Path = Path("data/curation/source_manifest.csv"),
) -> int:
    rows = read_csv_rows(input_path)
    registry = load_edition_registry(manifest_path)
    allowed_years = set(registry.held_editions)
    issues = []
    record_id_keys = defaultdict(list)
    title_keys = defaultdict(list)
    doi_keys = defaultdict(list)

    def add(row, severity, issue_type, detail):
        issues.append(
            {
                "severity": severity,
                "issue_type": issue_type,
                "record_id": row.get("record_id", ""),
                "publication_year": row.get("publication_year", ""),
                "paper_title": row.get("paper_title", ""),
                "detail": detail,
            }
        )

    for row in rows:
        record_id = row.get("record_id", "").strip()
        if not record_id:
            add(row, "error", "missing_record_id", "Stable record ID is blank.")
        else:
            record_id_keys[record_id].append(row)
        try:
            year = int(row.get("publication_year", 0))
        except ValueError:
            year = 0
        if year not in allowed_years:
            add(
                row,
                "error",
                "invalid_year",
                f"Year {year} is outside the 3DV edition set.",
            )
        if not row.get("paper_title"):
            add(row, "error", "missing_title", "Canonical title is blank.")
        if not row.get("authors"):
            add(row, "error", "missing_authors", "Canonical author list is blank.")
        if is_admin_record(row.get("paper_title", "")):
            add(
                row,
                "error",
                "administrative_record",
                "Administrative proceedings material is present.",
            )
        if not row.get("abstract"):
            add(
                row,
                "warning",
                "missing_abstract",
                "Topic classification will rely on weaker metadata.",
            )
        if not row.get("openalex_id"):
            fallback_source = row.get("citation_primary_source", "")
            fallback_count = row.get("citation_primary_count", "")
            detail = (
                f"OpenAlex metadata is unavailable; citation fallback {fallback_source}="
                f"{fallback_count} is recorded."
                if fallback_source
                and fallback_source != "OpenAlex"
                and fallback_count != ""
                else "OpenAlex metadata and citation count are unavailable."
            )
            add(row, "warning", "missing_openalex_match", detail)
        if not row.get("primary_topic"):
            add(
                row,
                "warning",
                "missing_primary_topic",
                "Atlas primary category is unavailable.",
            )

        primary_source = row.get("citation_primary_source", "").strip()
        primary_count = row.get("citation_primary_count", "").strip()
        primary_date = row.get("citation_snapshot_date", "").strip()
        if not primary_source or not primary_count or not primary_date:
            add(
                row,
                "error",
                "missing_citation_provenance",
                "Primary citation source, count, and snapshot date are required.",
            )
        elif not primary_count.isdigit():
            add(
                row,
                "error",
                "invalid_primary_citation_count",
                "Primary citation count must be a non-negative integer.",
            )
        if primary_source == "OpenAlex" and (
            not row.get("openalex_id")
            or primary_count != row.get("openalex_cited_by_count", "").strip()
            or primary_date != row.get("openalex_snapshot_date", "").strip()
        ):
            add(
                row,
                "error",
                "citation_provenance_mismatch",
                "Primary OpenAlex citation fields disagree with their provider fields.",
            )

        title_keys[(year, normalize_title(row.get("paper_title", "")))].append(row)
        doi = normalize_doi(row.get("doi", ""))
        if doi:
            doi_keys[doi].append(row)

    for record_id, duplicates in record_id_keys.items():
        if len(duplicates) > 1:
            for row in duplicates:
                add(
                    row,
                    "error",
                    "duplicate_record_id",
                    f"Record ID {record_id} appears {len(duplicates)} times.",
                )
    for key, duplicates in title_keys.items():
        if key[1] and len(duplicates) > 1:
            for row in duplicates:
                add(
                    row,
                    "error",
                    "duplicate_title_year",
                    f"{len(duplicates)} rows share this normalized title/year.",
                )
    for doi, duplicates in doi_keys.items():
        if len(duplicates) > 1:
            for row in duplicates:
                add(
                    row,
                    "error",
                    "duplicate_doi",
                    f"DOI {doi} appears {len(duplicates)} times.",
                )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv_rows(
        output_dir / "validation_issues.csv",
        issues,
        [
            "severity",
            "issue_type",
            "record_id",
            "publication_year",
            "paper_title",
            "detail",
        ],
    )

    coverage = []
    years = sorted(allowed_years)
    for year in years:
        subset = [r for r in rows if int(r.get("publication_year") or 0) == year]
        count = len(subset)
        coverage.append(
            {
                "year": year,
                "paper_rows": count,
                "abstract_rows": sum(bool(r.get("abstract")) for r in subset),
                "abstract_coverage": round(
                    sum(bool(r.get("abstract")) for r in subset) / count, 6
                )
                if count
                else 0,
                "openalex_rows": sum(bool(r.get("openalex_id")) for r in subset),
                "openalex_coverage": round(
                    sum(bool(r.get("openalex_id")) for r in subset) / count, 6
                )
                if count
                else 0,
                "semantic_scholar_rows": sum(
                    bool(r.get("s2_paper_id")) for r in subset
                ),
                "semantic_scholar_coverage": round(
                    sum(bool(r.get("s2_paper_id")) for r in subset) / count, 6
                )
                if count
                else 0,
                "classified_rows": sum(bool(r.get("primary_topic")) for r in subset),
                "classification_coverage": round(
                    sum(bool(r.get("primary_topic")) for r in subset) / count, 6
                )
                if count
                else 0,
            }
        )
    write_csv_rows(output_dir / "validation_coverage.csv", coverage)

    metadata_review = [
        metadata_review_row(row) for row in rows if metadata_review_reasons(row)
    ]
    write_csv_rows(
        output_dir / "metadata_review_queue.csv",
        metadata_review,
        METADATA_REVIEW_FIELDS,
    )
    refresh_coverage_report(output_dir, rows, registry)

    counts = Counter(issue["severity"] for issue in issues)
    print(
        f"Validation: {len(rows)} rows, {counts['error']} errors, "
        f"{counts['warning']} warnings, {len(metadata_review)} metadata review rows."
    )
    if strict and counts["error"]:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the 3DV corpus.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/qa"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/curation/source_manifest.csv"),
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    raise SystemExit(validate(args.input, args.output_dir, args.strict, args.manifest))


if __name__ == "__main__":
    main()
