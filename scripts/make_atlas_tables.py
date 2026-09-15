from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import read_csv_rows, write_csv_rows


def split_tags(value: str) -> list[str]:
    return [x.strip() for x in str(value or "").split(";") if x.strip()]


def aggregate(
    rows: list[dict[str, str]],
    years: list[int],
    entries: list[dict],
    field: str,
    multilabel: bool,
    taxonomy_version: str,
    category_type: str,
) -> list[dict[str, object]]:
    by_year: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["publication_year"])].append(row)

    output: list[dict[str, object]] = []
    for year in years:
        year_rows = by_year[year]
        paper_count = len(year_rows)
        counts: Counter[str] = Counter()
        for row in year_rows:
            labels = (
                split_tags(row.get(field, "")) if multilabel else [row.get(field, "")]
            )
            for label in labels:
                if label:
                    counts[label] += 1
        for display_order, entry in enumerate(entries, start=1):
            category = entry["id"]
            count = counts[category]
            output.append(
                {
                    "taxonomy_version": taxonomy_version,
                    "category_type": category_type,
                    "year": year,
                    "category": category,
                    "label": entry.get("label", category),
                    "short_label": entry.get(
                        "short_label", entry.get("label", category)
                    ),
                    "supergroup": entry.get("supergroup", ""),
                    "display_order": int(entry.get("display_order", display_order)),
                    "paper_count": paper_count,
                    "count": count,
                    "share_of_papers": round(count / paper_count, 6)
                    if paper_count
                    else 0,
                }
            )
    return output


def make_tables(input_path: Path, output_dir: Path, taxonomy_path: Path) -> None:
    rows = read_csv_rows(input_path)
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    taxonomy_version = taxonomy.get("version", "")
    years = sorted({int(r["publication_year"]) for r in rows})

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv_rows(
        output_dir / "atlas_primary_topics_by_year.csv",
        aggregate(
            rows,
            years,
            taxonomy["primary_topics"],
            "primary_topic",
            False,
            taxonomy_version,
            "primary",
        ),
    )
    write_csv_rows(
        output_dir / "atlas_methods_by_year.csv",
        aggregate(
            rows,
            years,
            taxonomy["method_tags"],
            "method_tags",
            True,
            taxonomy_version,
            "method_tag",
        ),
    )
    write_csv_rows(
        output_dir / "atlas_domains_by_year.csv",
        aggregate(
            rows,
            years,
            taxonomy["domain_tags"],
            "domain_tags",
            True,
            taxonomy_version,
            "domain_tag",
        ),
    )
    write_csv_rows(
        output_dir / "bar_category_order.csv",
        [
            {
                "display_order": int(topic["display_order"]),
                "supergroup": topic["supergroup"],
                "category_id": topic["id"],
                "label": topic["label"],
                "short_label": topic["short_label"],
                "taxonomy_version": taxonomy_version,
            }
            for topic in taxonomy["primary_topics"]
        ],
    )

    paper_counts = []
    for year in years:
        year_rows = [r for r in rows if int(r["publication_year"]) == year]
        paper_counts.append(
            {
                "taxonomy_version": taxonomy_version,
                "year": year,
                "paper_count": len(year_rows),
                "abstract_count": sum(bool(r.get("abstract")) for r in year_rows),
                "openalex_citation_count": sum(
                    bool(r.get("openalex_cited_by_count")) for r in year_rows
                ),
                "semantic_scholar_citation_count": sum(
                    bool(r.get("s2_citation_count")) for r in year_rows
                ),
                "google_scholar_citation_count": sum(
                    bool(r.get("google_scholar_citation_count")) for r in year_rows
                ),
                "classified_count": sum(
                    bool(r.get("primary_topic")) for r in year_rows
                ),
            }
        )
    write_csv_rows(input_path.parent / "paper_counts_by_year.csv", paper_counts)
    print(f"Wrote Atlas tables for {len(years)} editions.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create chart-ready Atlas tables.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/atlas"))
    parser.add_argument(
        "--taxonomy", type=Path, default=Path("config/topic_taxonomy.json")
    )
    args = parser.parse_args()
    make_tables(args.input, args.output_dir, args.taxonomy)


if __name__ == "__main__":
    main()
