from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import (
    flatten_names,
    load_json_cell,
    normalize_title,
    read_csv_rows,
    write_csv_rows,
)


def phrase_hits(text: str, patterns: list[str]) -> float:
    score = 0.0
    for pattern in patterns:
        norm = normalize_title(pattern)
        if not norm:
            continue
        if f" {norm} " in f" {text} ":
            score += 1.0 + min(1.0, len(norm.split()) * 0.15)
    return score


def metadata_text(row: dict[str, str]) -> str:
    values: list[str] = [row.get("source_keywords", "")]
    values.extend(flatten_names(load_json_cell(row.get("openalex_topics_json", ""))))
    values.extend(flatten_names(load_json_cell(row.get("openalex_keywords_json", ""))))
    values.extend(flatten_names(load_json_cell(row.get("s2_fields_of_study_json", ""))))
    return normalize_title(" ".join(values))


def category_score(
    category: dict,
    title: str,
    abstract: str,
    metadata: str,
) -> float:
    patterns = category.get("patterns", [])
    strong_patterns = category.get("strong_patterns", [])
    negative_patterns = category.get("negative_patterns", [])

    base = (
        3.0 * phrase_hits(title, patterns)
        + 1.0 * phrase_hits(abstract, patterns)
        + 1.7 * phrase_hits(metadata, patterns)
    )
    anchors = (
        5.0 * phrase_hits(title, strong_patterns)
        + 2.0 * phrase_hits(abstract, strong_patterns)
        + 2.5 * phrase_hits(metadata, strong_patterns)
    )
    penalty = (
        3.0 * phrase_hits(title, negative_patterns)
        + 1.0 * phrase_hits(abstract, negative_patterns)
        + 1.5 * phrase_hits(metadata, negative_patterns)
    )
    multiplier = float(category.get("score_multiplier", 1.0))
    return round(max(0.0, (base + anchors - penalty) * multiplier), 4)


def score_primary(row: dict[str, str], taxonomy: dict) -> list[tuple[str, float]]:
    title = normalize_title(row.get("paper_title", ""))
    abstract = normalize_title(row.get("abstract", ""))
    metadata = metadata_text(row)
    scored: list[tuple[str, float, int]] = []
    for category in taxonomy["primary_topics"]:
        scored.append(
            (
                category["id"],
                category_score(category, title, abstract, metadata),
                int(category.get("display_order", 999)),
            )
        )
    scored.sort(key=lambda item: (-item[1], item[2], item[0]))
    return [(category_id, score) for category_id, score, _ in scored]


def match_tags(row: dict[str, str], entries: list[dict]) -> list[str]:
    title = normalize_title(row.get("paper_title", ""))
    abstract = normalize_title(row.get("abstract", ""))
    metadata = metadata_text(row)
    combined = f"{title} {abstract} {metadata}"
    return [
        entry["id"]
        for entry in entries
        if phrase_hits(combined, entry.get("patterns", [])) > 0
    ]


def classify_row(row: dict[str, str], taxonomy: dict) -> dict[str, str]:
    ranked = score_primary(row, taxonomy)
    topic_by_id = {topic["id"]: topic for topic in taxonomy["primary_topics"]}
    top_id, top_score = ranked[0] if ranked else ("", 0.0)
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = round(top_score - second_score, 4)

    if top_score <= 0:
        primary = ""
        confidence = "low"
    else:
        primary = top_id
        if top_score >= 10.0 and margin >= 4.0:
            confidence = "high"
        elif top_score >= 5.0 and margin >= 2.0:
            confidence = "medium"
        else:
            confidence = "low"

    secondary = [
        category_id
        for category_id, score in ranked[1:3]
        if score > 0 and score >= max(1.5, top_score * 0.50)
    ]

    row["taxonomy_version"] = taxonomy.get("version", "")
    row["primary_topic"] = primary
    row["primary_topic_group"] = (
        topic_by_id.get(primary, {}).get("supergroup", "") if primary else ""
    )
    row["secondary_topics"] = "; ".join(secondary)
    row["method_tags"] = "; ".join(match_tags(row, taxonomy["method_tags"]))
    row["domain_tags"] = "; ".join(match_tags(row, taxonomy["domain_tags"]))
    row["topic_confidence"] = confidence
    row["topic_score_margin"] = margin
    needs_review = (
        confidence == "low" or not row.get("abstract") or not primary or margin < 1.5
    )
    row["topic_review_status"] = "needs_review" if needs_review else "auto_accepted"
    return row


def classify(
    input_path: Path,
    output_path: Path,
    taxonomy_path: Path,
    review_queue_path: Path | None = None,
    pending_only: bool = False,
    reviewed_ids: set[str] | None = None,
) -> None:
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    reviewed_ids = reviewed_ids or set()
    rows = read_csv_rows(input_path)
    fieldnames = list(rows[0].keys()) if rows else []
    for required in ["taxonomy_version", "primary_topic_group"]:
        if required not in fieldnames:
            insertion = (
                fieldnames.index("primary_topic")
                if "primary_topic" in fieldnames
                else len(fieldnames)
            )
            fieldnames.insert(insertion, required)
    classified_rows = []
    for row in rows:
        reviewed_in_ledger = (
            row.get("record_id") in reviewed_ids
            and row.get("taxonomy_version") == taxonomy.get("version")
            and bool(row.get("primary_topic"))
        )
        if pending_only and (
            row.get("topic_review_status") == "manually_reviewed" or reviewed_in_ledger
        ):
            row["topic_review_status"] = "manually_reviewed"
            classified_rows.append(row)
            continue
        classified = classify_row(row, taxonomy)
        if pending_only:
            classified["topic_review_status"] = "needs_review"
        classified_rows.append(classified)
    rows = classified_rows
    write_csv_rows(output_path, rows, fieldnames)
    review_rows = [r for r in rows if r.get("topic_review_status") == "needs_review"]
    review_fields = [
        "record_id",
        "publication_year",
        "paper_title",
        "abstract",
        "taxonomy_version",
        "primary_topic",
        "primary_topic_group",
        "secondary_topics",
        "method_tags",
        "domain_tags",
        "topic_confidence",
        "topic_score_margin",
        "topic_review_status",
        "manual_notes",
    ]
    if review_queue_path is not None:
        write_csv_rows(review_queue_path, review_rows, review_fields)
    print(f"Classified {len(rows)} rows; {len(review_rows)} require manual review.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify the 3DV Research Atlas.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=Path("config/topic_taxonomy.json"),
    )
    parser.add_argument("--review-queue", type=Path)
    parser.add_argument("--pending-only", action="store_true")
    args = parser.parse_args()
    classify(
        args.input,
        args.output or args.input,
        args.taxonomy,
        args.review_queue,
        args.pending_only,
    )


if __name__ == "__main__":
    main()
