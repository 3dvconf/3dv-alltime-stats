from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import (
    ensure_list,
    fetch_json,
    is_admin_record,
    merge_semicolon,
    normalize_doi,
    normalize_title,
    normalize_whitespace,
    read_csv_rows,
    stable_record_id,
    title_similarity,
    today_iso,
    write_csv_rows,
)

DBLP_SPARQL_ENDPOINT = "https://sparql.dblp.org/sparql"
DBLP_STREAM_IRI = "https://dblp.org/streams/conf/3dim"
PAPER_FIELDS = [
    "record_id",
    "publication_year",
    "edition_number",
    "paper_title",
    "authors",
    "author_count",
    "doi",
    "pages",
    "dblp_key",
    "dblp_url",
    "publisher_url",
    "paper_type",
    "abstract",
    "source_keywords",
    "openalex_id",
    "openalex_cited_by_count",
    "openalex_counts_by_year_json",
    "openalex_topics_json",
    "openalex_keywords_json",
    "openalex_snapshot_date",
    "openalex_match_method",
    "openalex_match_confidence",
    "s2_paper_id",
    "s2_citation_count",
    "s2_influential_citation_count",
    "s2_fields_of_study_json",
    "s2_snapshot_date",
    "s2_match_status",
    "google_scholar_citation_count",
    "citation_primary_source",
    "citation_primary_count",
    "citation_snapshot_date",
    "taxonomy_version",
    "primary_topic",
    "primary_topic_group",
    "secondary_topics",
    "method_tags",
    "domain_tags",
    "topic_confidence",
    "topic_score_margin",
    "topic_review_status",
    "source_provenance",
    "manual_notes",
]
DBLP_AUTHORITY_FIELDS = {
    "record_id",
    "publication_year",
    "edition_number",
    "paper_title",
    "authors",
    "author_count",
    "doi",
    "pages",
    "dblp_key",
    "dblp_url",
    "publisher_url",
    "paper_type",
}
SUPPLEMENTAL_FIELDS = {"abstract", "source_keywords", "source_provenance"}
CHANGE_FIELDS = ("paper_title", "authors", "doi", "pages", "publisher_url")


@dataclass(frozen=True)
class EditionRegistry:
    held_editions: dict[int, int]
    no_edition_years: set[int]

    @property
    def first_year(self) -> int:
        return min(set(self.held_editions) | self.no_edition_years)

    @property
    def last_year(self) -> int:
        return max(set(self.held_editions) | self.no_edition_years)


def load_edition_registry(path: Path) -> EditionRegistry:
    manifest = read_csv_rows(path)
    held: dict[int, int] = {}
    no_edition: set[int] = set()
    seen: set[int] = set()
    for row in manifest:
        try:
            year = int(row.get("year", ""))
        except ValueError as exc:
            raise ValueError(
                "Every source-manifest row needs an integer year."
            ) from exc
        if year in seen:
            raise ValueError(f"Duplicate source-manifest year: {year}")
        seen.add(year)
        status = row.get("edition_status", "").strip().casefold()
        if status == "held":
            try:
                held[year] = int(row.get("edition_number", ""))
            except ValueError as exc:
                raise ValueError(
                    f"Held edition {year} needs an integer edition_number."
                ) from exc
        elif status == "no edition":
            no_edition.add(year)
        else:
            raise ValueError(f"Unsupported edition_status for {year}: {status!r}")
    expected_numbers = list(range(1, len(held) + 1))
    actual_numbers = [held[year] for year in sorted(held)]
    if actual_numbers != expected_numbers:
        raise ValueError(
            "Held edition numbers must be sequential in chronological order."
        )
    if not held:
        raise ValueError("The source manifest has no held editions.")
    return EditionRegistry(held, no_edition)


def dblp_sparql_query(first_year: int, last_year: int) -> str:
    return f"""
PREFIX dblp: <https://dblp.org/rdf/schema#>
SELECT ?publ ?title ?year ?doi ?pages ?document ?authorName ?authorOrdinal WHERE {{
  ?publ a dblp:Inproceedings ;
        dblp:publishedInStream <{DBLP_STREAM_IRI}> ;
        dblp:title ?title ;
        dblp:yearOfPublication ?year .
  OPTIONAL {{ ?publ dblp:doi ?doi . }}
  OPTIONAL {{ ?publ dblp:pagination ?pages . }}
  OPTIONAL {{ ?publ dblp:primaryDocumentPage ?document . }}
  OPTIONAL {{
    ?publ dblp:hasSignature ?signature .
    ?signature dblp:signatureDblpName ?authorName ;
               dblp:signatureOrdinal ?authorOrdinal .
  }}
  FILTER (?year >= "{first_year}"^^<http://www.w3.org/2001/XMLSchema#gYear> &&
          ?year <= "{last_year}"^^<http://www.w3.org/2001/XMLSchema#gYear>)
}}
ORDER BY ?year ?publ ?authorOrdinal
""".strip()


def sparql_value(binding: dict, field: str) -> str:
    value = binding.get(field, {})
    return normalize_whitespace(
        value.get("value", "") if isinstance(value, dict) else ""
    )


def fetch_dblp_corpus(
    edition_by_year: dict[int, int],
    excluded_records: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    payload = fetch_json(
        DBLP_SPARQL_ENDPOINT,
        headers={"Content-Type": "application/sparql-query"},
        data=dblp_sparql_query(min(edition_by_year), max(edition_by_year)).encode(
            "utf-8"
        ),
        method="POST",
        timeout=120,
    )
    bindings = payload.get("results", {}).get("bindings", [])
    papers: dict[str, dict[str, object]] = {}
    authors: dict[str, dict[int, str]] = defaultdict(dict)
    excluded_urls: set[str] = set()

    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        try:
            year = int(sparql_value(binding, "year"))
        except ValueError:
            continue
        if year not in edition_by_year:
            continue
        dblp_url = sparql_value(binding, "publ")
        title = sparql_value(binding, "title")
        if not dblp_url or not title:
            continue
        dblp_key = dblp_url.split("/rec/", 1)[-1]
        doi = normalize_doi(sparql_value(binding, "doi"))
        if is_admin_record(title):
            if excluded_records is not None and dblp_url not in excluded_urls:
                excluded_records.append(
                    {
                        "record_id": stable_record_id(year, title, dblp_key),
                        "publication_year": year,
                        "paper_title": title,
                        "doi": doi,
                        "dblp_key": dblp_key,
                        "dblp_url": dblp_url,
                        "exclusion_reason": "administrative_record",
                        "exclusion_detail": "Title matches the non-research proceedings-material filter.",
                    }
                )
                excluded_urls.add(dblp_url)
            continue
        if dblp_url not in papers:
            row = {field: "" for field in PAPER_FIELDS}
            row.update(
                {
                    "record_id": stable_record_id(year, title, dblp_key),
                    "publication_year": year,
                    "edition_number": edition_by_year[year],
                    "paper_title": title,
                    "doi": doi,
                    "pages": sparql_value(binding, "pages"),
                    "dblp_key": dblp_key,
                    "dblp_url": dblp_url,
                    "publisher_url": sparql_value(binding, "document")
                    or (f"https://doi.org/{doi}" if doi else ""),
                    "paper_type": "Conference and Workshop Papers",
                    "source_provenance": "DBLP",
                }
            )
            papers[dblp_url] = row

        author_name = sparql_value(binding, "authorName")
        if author_name:
            try:
                ordinal = int(sparql_value(binding, "authorOrdinal"))
            except ValueError:
                ordinal = len(authors[dblp_url]) + 1
            authors[dblp_url][ordinal] = author_name

    rows = list(papers.values())
    for row in rows:
        ordered_authors = [
            name for _, name in sorted(authors.get(str(row["dblp_url"]), {}).items())
        ]
        row["authors"] = "; ".join(ordered_authors)
        row["author_count"] = len(ordered_authors)
    return rows


def load_supplemental_records(url: str) -> list[dict]:
    if not url:
        return []
    payload = fetch_json(url)
    records = payload.get("records", []) if isinstance(payload, dict) else []
    return [
        r
        for r in records
        if isinstance(r, dict) and not is_admin_record(r.get("title", ""))
    ]


def index_supplemental(
    records: list[dict],
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    by_doi: dict[str, dict] = {}
    by_title: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        doi = normalize_doi(record.get("doi", ""))
        if doi:
            by_doi[doi] = record
        title = normalize_title(record.get("title", ""))
        if title:
            by_title[title].append(record)
    return by_doi, by_title


def find_supplemental_match(
    row: dict[str, object],
    records: list[dict],
    by_doi: dict[str, dict],
    by_title: dict[str, list[dict]],
) -> tuple[dict | None, str, float]:
    doi = normalize_doi(row.get("doi", ""))
    if doi and doi in by_doi:
        return by_doi[doi], "doi_exact", 1.0
    norm_title = normalize_title(row.get("paper_title", ""))
    if norm_title in by_title and len(by_title[norm_title]) == 1:
        return by_title[norm_title][0], "title_exact", 0.99
    best = None
    best_score = 0.0
    for candidate in records:
        score = title_similarity(row.get("paper_title", ""), candidate.get("title", ""))
        if score > best_score:
            best, best_score = candidate, score
    if best is not None and best_score >= 0.96:
        return best, "title_fuzzy", best_score
    return None, "not_found", best_score


def keywords_from_record(record: dict) -> list[str]:
    values = record.get("keywords", [])
    if isinstance(values, str):
        return [x.strip() for x in values.replace(",", ";").split(";") if x.strip()]
    out: list[str] = []
    for item in ensure_list(values):
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = (
                item.get("name")
                or item.get("display_name")
                or item.get("keyword")
                or ""
            )
        else:
            value = str(item)
        value = normalize_whitespace(value)
        if value:
            out.append(value)
    return out


def merge_supplemental(
    rows: list[dict[str, object]],
    source_manifest: list[dict[str, str]],
    edition_years: list[int],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    manifest_by_year = {int(r["year"]): r for r in source_manifest if r.get("year")}
    audit: list[dict[str, object]] = []
    for year in edition_years:
        manifest = manifest_by_year.get(year, {})
        url = manifest.get("supplemental_metadata_url", "")
        if not url:
            continue
        records = load_supplemental_records(url)
        by_doi, by_title = index_supplemental(records)
        year_rows = [r for r in rows if int(r["publication_year"]) == year]
        for row in year_rows:
            match, method, confidence = find_supplemental_match(
                row, records, by_doi, by_title
            )
            audit.append(
                {
                    "record_id": row["record_id"],
                    "publication_year": year,
                    "paper_title": row["paper_title"],
                    "supplemental_match_method": method,
                    "supplemental_match_confidence": round(confidence, 4),
                    "supplemental_title": match.get("title", "") if match else "",
                }
            )
            if not match:
                continue
            if not row.get("abstract"):
                row["abstract"] = normalize_whitespace(match.get("abstract", ""))
            row["source_keywords"] = merge_semicolon(
                row.get("source_keywords", ""), keywords_from_record(match)
            )
            if not row.get("doi"):
                row["doi"] = normalize_doi(match.get("doi", ""))
            if not row.get("publisher_url"):
                row["publisher_url"] = normalize_whitespace(
                    match.get("paper_url") or match.get("pdf_url") or ""
                )
            source_name = (
                manifest.get("supplemental_source_type") or "supplemental snapshot"
            )
            row["source_provenance"] = merge_semicolon(
                row.get("source_provenance", ""), [source_name]
            )
    return rows, audit


def replay_google_scholar_snapshot(
    rows: list[dict[str, object]], snapshot_path: Path
) -> None:
    if not snapshot_path.exists():
        return

    snapshot_rows = read_csv_rows(snapshot_path)
    by_id = {str(row["record_id"]): row for row in rows}
    seen: set[str] = set()
    for snapshot in snapshot_rows:
        record_id = snapshot.get("record_id", "").strip()
        if not record_id:
            raise ValueError("Google Scholar snapshot contains a blank record_id")
        if record_id in seen:
            raise ValueError(
                f"duplicate Google Scholar snapshot record_id: {record_id}"
            )
        seen.add(record_id)
        if record_id not in by_id:
            raise ValueError(f"unknown Google Scholar snapshot record_id: {record_id}")

        count = snapshot.get("google_scholar_citation_count", "").strip()
        if not count.isdigit():
            raise ValueError(
                f"invalid Google Scholar citation count for {record_id}: {count!r}"
            )
        corpus_row = by_id[record_id]
        snapshot_title = snapshot.get("paper_title", "").strip()
        if snapshot_title and normalize_title(snapshot_title) != normalize_title(
            corpus_row.get("paper_title", "")
        ):
            raise ValueError(f"Google Scholar title mismatch for {record_id}")
        snapshot_year = snapshot.get("publication_year", "").strip()
        if snapshot_year and snapshot_year != str(
            corpus_row.get("publication_year", "")
        ):
            raise ValueError(f"Google Scholar year mismatch for {record_id}")
        corpus_row["google_scholar_citation_count"] = int(count)


def merge_existing_release(
    fresh_rows: list[dict[str, object]],
    existing_rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    fresh_ids = {str(row.get("record_id", "")) for row in fresh_rows}
    existing_by_id = {
        str(row.get("record_id", "")): row
        for row in existing_rows
        if row.get("record_id")
    }
    removed = sorted(set(existing_by_id) - fresh_ids)
    if removed:
        preview = ", ".join(removed[:5])
        suffix = "..." if len(removed) > 5 else ""
        raise ValueError(f"historical records disappeared from DBLP: {preview}{suffix}")

    merged_rows: list[dict[str, object]] = []
    changes: list[dict[str, object]] = []
    for fresh in fresh_rows:
        record_id = str(fresh.get("record_id", ""))
        merged = {field: "" for field in PAPER_FIELDS}
        merged.update(fresh)
        existing = existing_by_id.get(record_id)
        if existing is None:
            changes.append(
                {
                    "change_type": "added",
                    "record_id": record_id,
                    "publication_year": fresh.get("publication_year", ""),
                    "paper_title": fresh.get("paper_title", ""),
                    "changed_fields": "",
                }
            )
            merged_rows.append(merged)
            continue

        changed_fields = [
            field
            for field in CHANGE_FIELDS
            if str(existing.get(field, "")) != str(fresh.get(field, ""))
        ]
        if changed_fields:
            changes.append(
                {
                    "change_type": "bibliographic_change",
                    "record_id": record_id,
                    "publication_year": fresh.get("publication_year", ""),
                    "paper_title": fresh.get("paper_title", ""),
                    "changed_fields": "; ".join(changed_fields),
                }
            )

        for field in PAPER_FIELDS:
            if field in DBLP_AUTHORITY_FIELDS:
                continue
            if field in SUPPLEMENTAL_FIELDS:
                if not merged.get(field) and existing.get(field):
                    merged[field] = existing[field]
                continue
            if existing.get(field, "") != "":
                merged[field] = existing[field]
        merged_rows.append(merged)
    return merged_rows, changes


def build(
    output_dir: Path,
    manifest_path: Path,
    *,
    existing_path: Path | None = None,
    audit_dir: Path | None = None,
    qa_dir: Path | None = None,
) -> None:
    manifest = read_csv_rows(manifest_path)
    registry = load_edition_registry(manifest_path)
    edition_by_year = registry.held_editions
    edition_years = sorted(edition_by_year)
    excluded_records: list[dict[str, object]] = []
    rows = fetch_dblp_corpus(edition_by_year, excluded_records)
    coverage: list[dict[str, object]] = []
    for year in edition_years:
        year_rows = [r for r in rows if int(r["publication_year"]) == year]
        coverage.append(
            {
                "year": year,
                "canonical_source": "DBLP",
                "canonical_paper_rows": len(year_rows),
                "abstract_rows_before_supplement": sum(
                    bool(r.get("abstract")) for r in year_rows
                ),
                "fetched_at": today_iso(),
            }
        )
        print(f"DBLP {year}: {len(year_rows)} paper rows", flush=True)

    rows, supplement_audit = merge_supplemental(rows, manifest, edition_years)
    changes: list[dict[str, object]] = []
    if existing_path is not None and existing_path.exists():
        rows, changes = merge_existing_release(rows, read_csv_rows(existing_path))
    rows.sort(
        key=lambda r: (int(r["publication_year"]), normalize_title(r["paper_title"]))
    )
    if existing_path is None:
        replay_google_scholar_snapshot(
            rows, output_dir / "google_scholar_citations.csv"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    curation_dir = audit_dir or output_dir / "curation"
    qa_dir = qa_dir or output_dir.parent / "outputs" / "qa"
    write_csv_rows(output_dir / "3dv_papers.csv", rows, PAPER_FIELDS)
    write_csv_rows(curation_dir / "supplemental_match_audit.csv", supplement_audit)
    write_csv_rows(
        curation_dir / "excluded_records.csv",
        excluded_records,
        [
            "record_id",
            "publication_year",
            "paper_title",
            "doi",
            "dblp_key",
            "dblp_url",
            "exclusion_reason",
            "exclusion_detail",
        ],
    )

    for item in coverage:
        year_rows = [r for r in rows if int(r["publication_year"]) == int(item["year"])]
        item["abstract_rows_after_supplement"] = sum(
            bool(r.get("abstract")) for r in year_rows
        )
        item["keyword_rows_after_supplement"] = sum(
            bool(r.get("source_keywords")) for r in year_rows
        )
    write_csv_rows(qa_dir / "build_coverage.csv", coverage)
    if existing_path is not None:
        write_csv_rows(
            qa_dir / "update_diff.csv",
            changes,
            [
                "change_type",
                "record_id",
                "publication_year",
                "paper_title",
                "changed_fields",
            ],
        )
    print(f"Wrote {len(rows)} canonical paper rows to {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build canonical 3DV paper corpus from DBLP."
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/curation/source_manifest.csv")
    )
    args = parser.parse_args()
    build(args.output_dir, args.manifest)


if __name__ == "__main__":
    main()
