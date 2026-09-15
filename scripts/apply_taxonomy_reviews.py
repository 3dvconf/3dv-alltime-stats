from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from core import merge_semicolon, read_csv_rows, write_csv_rows

REQUIRED_REVIEW_FIELDS = {
    "record_id",
    "publication_year",
    "paper_title",
    "primary_topic",
    "secondary_topics",
    "review_confidence",
    "review_note",
    "reviewer",
    "reviewed_at",
}
VALID_REVIEW_CONFIDENCE = {"high", "medium", "low"}


def _listed(values: set[str]) -> str:
    return ", ".join(sorted(values))


def _split_topics(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def apply_taxonomy_reviews(
    input_path: Path,
    reviews_path: Path,
    taxonomy_path: Path,
    output_path: Path,
) -> None:
    rows = read_csv_rows(input_path)
    reviews = read_csv_rows(reviews_path)
    if not rows:
        raise ValueError("Corpus is empty; expert reviews cannot be applied.")
    if not reviews:
        raise ValueError("Expert review ledger is empty.")

    missing_columns = REQUIRED_REVIEW_FIELDS - set(reviews[0])
    if missing_columns:
        raise ValueError(
            f"Review ledger is missing columns: {_listed(missing_columns)}"
        )

    corpus_ids = [row.get("record_id", "") for row in rows]
    if not all(corpus_ids) or len(corpus_ids) != len(set(corpus_ids)):
        raise ValueError("Corpus record_id values must be present and unique.")

    review_ids = [review.get("record_id", "") for review in reviews]
    duplicate_ids = {
        record_id
        for record_id, count in Counter(review_ids).items()
        if record_id and count > 1
    }
    if duplicate_ids:
        raise ValueError(f"duplicate review record_id: {_listed(duplicate_ids)}")
    if not all(review_ids):
        raise ValueError("Review record_id values must be present.")

    corpus_id_set = set(corpus_ids)
    review_id_set = set(review_ids)
    unknown_ids = review_id_set - corpus_id_set
    if unknown_ids:
        raise ValueError(
            f"unknown corpus record_id in review ledger: {_listed(unknown_ids)}"
        )
    missing_ids = corpus_id_set - review_id_set
    if missing_ids:
        raise ValueError(f"missing review record_id: {_listed(missing_ids)}")

    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    taxonomy_version = str(taxonomy.get("version", "")).strip()
    if not taxonomy_version:
        raise ValueError("Taxonomy configuration is missing a version.")
    topic_to_group = {
        topic["id"]: topic["supergroup"] for topic in taxonomy.get("primary_topics", [])
    }
    if not topic_to_group:
        raise ValueError("Taxonomy configuration has no primary topics.")

    by_corpus_id = {row["record_id"]: row for row in rows}
    normalized_secondaries: dict[str, list[str]] = {}
    for review in reviews:
        record_id = review["record_id"]
        corpus_row = by_corpus_id[record_id]

        if (
            review.get("publication_year", "").strip()
            != corpus_row.get("publication_year", "").strip()
        ):
            raise ValueError(f"publication_year mismatch for {record_id}")
        if (
            review.get("paper_title", "").strip()
            != corpus_row.get("paper_title", "").strip()
        ):
            raise ValueError(f"paper_title mismatch for {record_id}")

        primary = review.get("primary_topic", "").strip()
        if primary not in topic_to_group:
            raise ValueError(f"invalid primary_topic for {record_id}: {primary}")

        secondary = _split_topics(review.get("secondary_topics", ""))
        if len(secondary) > 2:
            raise ValueError(
                f"at most two secondary_topics are allowed for {record_id}"
            )
        if len(secondary) != len(set(secondary)):
            raise ValueError(f"duplicate secondary_topics for {record_id}")
        if primary in secondary:
            raise ValueError(
                f"primary_topic repeated in secondary_topics for {record_id}"
            )
        invalid_secondary = {
            topic for topic in secondary if topic not in topic_to_group
        }
        if invalid_secondary:
            raise ValueError(
                f"invalid secondary_topics for {record_id}: "
                f"{_listed(invalid_secondary)}"
            )
        normalized_secondaries[record_id] = secondary

        confidence = review.get("review_confidence", "").strip().casefold()
        if confidence not in VALID_REVIEW_CONFIDENCE:
            raise ValueError(
                f"invalid review_confidence for {record_id}: "
                f"{review.get('review_confidence', '')}"
            )

    by_review_id = {review["record_id"]: review for review in reviews}
    for row in rows:
        review = by_review_id[row["record_id"]]
        primary = review["primary_topic"].strip()
        confidence = review["review_confidence"].strip().casefold()
        row["taxonomy_version"] = taxonomy_version
        row["primary_topic"] = primary
        row["primary_topic_group"] = topic_to_group[primary]
        row["secondary_topics"] = "; ".join(normalized_secondaries[row["record_id"]])
        row["topic_confidence"] = confidence
        row["topic_score_margin"] = ""
        row["topic_review_status"] = "manually_reviewed"
        provenance = (
            f"expert_taxonomy_review_v{taxonomy_version}:"
            f"{review['reviewed_at'].strip()}:{review['reviewer'].strip()}"
        )
        row["manual_notes"] = merge_semicolon(row.get("manual_notes", ""), [provenance])

    fieldnames = list(rows[0].keys())
    write_csv_rows(output_path, rows, fieldnames)
    confidence_counts = Counter(
        review["review_confidence"].strip().casefold() for review in reviews
    )
    secondary_counts = Counter(
        len(normalized_secondaries[review["record_id"]]) for review in reviews
    )
    print(
        f"Applied {len(reviews)} expert taxonomy v{taxonomy_version} reviews "
        f"({confidence_counts['high']} high, {confidence_counts['medium']} medium, "
        f"{confidence_counts['low']} low confidence; "
        f"{secondary_counts[0]} with no secondary topic, "
        f"{secondary_counts[1]} with one, {secondary_counts[2]} with two)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and apply the complete expert taxonomy decision ledger."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    apply_taxonomy_reviews(
        args.input,
        args.reviews,
        args.taxonomy,
        args.output or args.input,
    )


if __name__ == "__main__":
    main()
