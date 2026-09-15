from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import MutableMapping

from core import normalize_title, read_csv_rows, today_iso, write_csv_rows

SCHOLAR_MATCH_METHODS = {
    "matched_exact_context",
    "matched_human_verified",
    "not_found",
}
SCHOLAR_SNAPSHOT_FIELDS = [
    "record_id",
    "paper_title",
    "publication_year",
    "google_scholar_citation_count",
    "snapshot_date",
    "match_method",
]
SCHOLAR_REVIEW_FIELDS = [
    "record_id",
    "paper_title",
    "publication_year",
    "openalex_cited_by_count",
    "openalex_rank",
    "s2_citation_count",
    "semantic_scholar_rank",
    "google_scholar_citation_count",
    "snapshot_date",
    "match_method",
]
TAXONOMY_REPLAY_FIELDS = [
    "taxonomy_version",
    "primary_topic",
    "primary_topic_group",
    "secondary_topics",
]
PUBLISHED_TAXONOMY_INTERNAL_FIELDS = {
    "topic_confidence",
    "topic_score_margin",
    "topic_review_status",
}


def load_env_file(
    path: Path,
    environment: MutableMapping[str, str],
) -> list[str]:
    if not path.exists():
        return []
    loaded: list[str] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid env line {line_number} in {path}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid env name on line {line_number} in {path}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in environment:
            environment[key] = value
            loaded.append(key)
    return loaded


def _ranked_ids(
    rows: list[dict[str, str]], field: str, limit: int
) -> tuple[list[str], dict[str, int]]:
    eligible = [
        (position, row)
        for position, row in enumerate(rows)
        if str(row.get(field, "")).strip() != ""
    ]
    eligible.sort(key=lambda item: (-int(item[1][field]), item[0]))
    ids = [row["record_id"] for _, row in eligible[:limit]]
    return ids, {record_id: rank for rank, record_id in enumerate(ids, start=1)}


def google_scholar_review_rows(
    papers: list[dict[str, str]],
    previous_snapshot: list[dict[str, str]],
    refresh: bool,
    limit: int = 50,
) -> list[dict[str, object]]:
    openalex_ids, openalex_ranks = _ranked_ids(papers, "openalex_cited_by_count", limit)
    semantic_ids, semantic_ranks = _ranked_ids(papers, "s2_citation_count", limit)
    candidate_ids = set(openalex_ids) | set(semantic_ids)
    previous_by_id = {row.get("record_id", ""): row for row in previous_snapshot}
    papers_by_id = {row["record_id"]: row for row in papers}
    ordered_ids = sorted(
        candidate_ids,
        key=lambda record_id: (
            min(
                openalex_ranks.get(record_id, limit + 1),
                semantic_ranks.get(record_id, limit + 1),
            ),
            normalize_title(papers_by_id[record_id].get("paper_title", "")),
            record_id,
        ),
    )
    queue: list[dict[str, object]] = []
    for record_id in ordered_ids:
        paper = papers_by_id[record_id]
        previous = previous_by_id.get(record_id, {}) if not refresh else {}
        if previous and (
            previous.get("paper_title", "") != paper.get("paper_title", "")
            or str(previous.get("publication_year", ""))
            != str(paper.get("publication_year", ""))
        ):
            previous = {}
        queue.append(
            {
                "record_id": record_id,
                "paper_title": paper.get("paper_title", ""),
                "publication_year": paper.get("publication_year", ""),
                "openalex_cited_by_count": paper.get("openalex_cited_by_count", ""),
                "openalex_rank": openalex_ranks.get(record_id, ""),
                "s2_citation_count": paper.get("s2_citation_count", ""),
                "semantic_scholar_rank": semantic_ranks.get(record_id, ""),
                "google_scholar_citation_count": previous.get(
                    "google_scholar_citation_count", ""
                ),
                "snapshot_date": previous.get("snapshot_date", ""),
                "match_method": previous.get("match_method", ""),
            }
        )
    return queue


def validate_google_scholar_review(
    papers: list[dict[str, str]],
    review_rows: list[dict[str, str]],
    limit: int = 50,
) -> list[dict[str, str]]:
    expected = google_scholar_review_rows(papers, [], True, limit)
    expected_ids = {row["record_id"] for row in expected}
    actual_ids = [row.get("record_id", "") for row in review_rows]
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("Google Scholar review contains duplicate record IDs.")
    if set(actual_ids) != expected_ids:
        raise ValueError(
            "Google Scholar review candidate set does not match provider ranks."
        )

    papers_by_id = {row["record_id"]: row for row in papers}
    snapshot: list[dict[str, str]] = []
    for review in review_rows:
        record_id = review["record_id"]
        paper = papers_by_id[record_id]
        if review.get("paper_title", "") != paper.get("paper_title", ""):
            raise ValueError(f"Google Scholar title mismatch for {record_id}")
        if str(review.get("publication_year", "")) != str(
            paper.get("publication_year", "")
        ):
            raise ValueError(f"Google Scholar year mismatch for {record_id}")
        method = review.get("match_method", "").strip()
        if method not in SCHOLAR_MATCH_METHODS:
            raise ValueError(f"Invalid Google Scholar match method for {record_id}")
        snapshot_date = review.get("snapshot_date", "").strip()
        try:
            date.fromisoformat(snapshot_date)
        except ValueError as exc:
            raise ValueError(
                f"Invalid Google Scholar snapshot date for {record_id}"
            ) from exc
        count = review.get("google_scholar_citation_count", "").strip()
        if method == "not_found":
            if count:
                raise ValueError(
                    f"Google Scholar not_found row has a count for {record_id}"
                )
        elif not count.isdigit():
            raise ValueError(f"Missing Google Scholar citation count for {record_id}")
        snapshot.append(
            {
                "record_id": record_id,
                "paper_title": paper.get("paper_title", ""),
                "publication_year": str(paper.get("publication_year", "")),
                "google_scholar_citation_count": count,
                "snapshot_date": snapshot_date,
                "match_method": method,
            }
        )
    snapshot.sort(
        key=lambda row: (
            -(
                int(row["google_scholar_citation_count"])
                if row["google_scholar_citation_count"]
                else -1
            ),
            normalize_title(row["paper_title"]),
        )
    )
    return snapshot


def apply_google_scholar_review(
    papers: list[dict[str, str]], snapshot: list[dict[str, str]]
) -> list[dict[str, str]]:
    counts = {
        row["record_id"]: row["google_scholar_citation_count"] for row in snapshot
    }
    updated: list[dict[str, str]] = []
    for source in papers:
        row = dict(source)
        row["google_scholar_citation_count"] = counts.get(row["record_id"], "")
        updated.append(row)
    return updated


def validate_google_scholar_replay(
    papers: list[dict[str, str]], snapshot: list[dict[str, str]]
) -> None:
    expected = {
        row["record_id"]: row.get("google_scholar_citation_count", "")
        for row in snapshot
    }
    for paper in papers:
        actual = paper.get("google_scholar_citation_count", "")
        if actual != expected.get(paper["record_id"], ""):
            raise ValueError(
                f"Published Google Scholar count does not replay for "
                f"{paper['record_id']}."
            )


def validate_taxonomy_replay(
    papers: list[dict[str, str]], replayed: list[dict[str, str]]
) -> None:
    replayed_by_id = {row["record_id"]: row for row in replayed}
    if {row["record_id"] for row in papers} != set(replayed_by_id):
        raise ValueError("Published taxonomy IDs do not match the replayed ledger.")
    for paper in papers:
        expected = replayed_by_id[paper["record_id"]]
        changed = [
            field
            for field in TAXONOMY_REPLAY_FIELDS
            if paper.get(field, "") != expected.get(field, "")
        ]
        if changed:
            raise ValueError(
                f"Published taxonomy does not replay for {paper['record_id']}: "
                f"{', '.join(changed)}."
            )


def published_paper_rows(
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    published: list[dict[str, str]] = []
    for source in rows:
        row = {
            field: value
            for field, value in source.items()
            if field not in PUBLISHED_TAXONOMY_INTERNAL_FIELDS
        }
        notes = [
            part.strip()
            for part in row.get("manual_notes", "").split(";")
            if part.strip() and not part.strip().startswith("expert_taxonomy_review")
        ]
        row["manual_notes"] = "; ".join(notes)
        published.append(row)
    return published


def validate_conference_statistics(
    rows: list[dict[str, str]], registry: object
) -> None:
    held_editions = getattr(registry, "held_editions")
    expected_years = set(held_editions)
    actual_years = [int(row.get("conference_year", "0") or 0) for row in rows]
    if len(actual_years) != len(set(actual_years)):
        raise ValueError("Conference statistics contain duplicate years.")
    if set(actual_years) != expected_years:
        raise ValueError("Conference statistics do not cover every held edition.")
    for row in rows:
        year = int(row["conference_year"])
        if int(row.get("edition_number", held_editions[year])) != held_editions[year]:
            raise ValueError(f"Conference edition number mismatch for {year}.")
        try:
            submissions = int(row["valid_full_paper_submissions"])
            accepted = int(row["accepted_full_papers"])
            oral = int(row["accepted_oral_papers"])
            spotlight = int(row["accepted_spotlight_papers"])
            poster = int(row["accepted_poster_papers"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid acceptance counts for {year}.") from exc
        if min(submissions, accepted, oral, spotlight, poster) < 0:
            raise ValueError(f"Conference counts cannot be negative for {year}.")
        if accepted > submissions:
            raise ValueError(f"Accepted papers cannot exceed submissions for {year}.")
        presentation_total = oral + spotlight + poster
        if presentation_total != accepted:
            raise ValueError(
                f"Conference presentation counts do not reconcile for {year}."
            )
        for field in ("acceptance_rate_pct", "oral_acceptance_rate_pct"):
            value = row.get(field, "").strip()
            if value and not re.fullmatch(r"\d+\.\d{2}", value):
                raise ValueError(f"{field} must use two decimals for {year}.")
            if value and not 0 <= float(value) <= 100:
                raise ValueError(f"{field} must be between 0 and 100 for {year}.")
        has_spotlight = row.get("has_spotlight_category", "").strip().casefold()
        if has_spotlight not in {"yes", "no"}:
            raise ValueError(f"Invalid spotlight-category flag for {year}.")
        if has_spotlight == "no" and int(row["accepted_spotlight_papers"]) != 0:
            raise ValueError(f"Spotlight count must be zero for {year}.")


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stage_metadata(path: Path) -> dict[str, str]:
    return {row["key"]: row["value"] for row in read_csv_rows(path) if row.get("key")}


def stage_release(
    project: Path,
    *,
    refresh_citations: bool = False,
    skip_openalex: bool = False,
    skip_semantic_scholar: bool = False,
    restart: bool = False,
    environment: MutableMapping[str, str] | None = None,
) -> None:
    from build_3dv_corpus import build
    from classify_topics import classify
    from enrich_openalex import enrich as enrich_openalex
    from enrich_semantic_scholar import enrich as enrich_semantic_scholar
    from validate_corpus import validate

    project = Path(project).resolve()
    data_dir = project / "data"
    curation_dir = data_dir / "curation"
    work_dir = project / ".work"
    candidate_path = work_dir / "3dv_papers.csv"
    metadata_path = work_dir / "stage_metadata.csv"
    manifest_path = curation_dir / "source_manifest.csv"
    canonical_path = data_dir / "3dv_papers.csv"
    taxonomy_path = project / "config/topic_taxonomy.json"
    if restart and work_dir.exists():
        shutil.rmtree(work_dir)

    manifest_hash = _sha256(manifest_path)
    resuming = candidate_path.exists()
    if resuming:
        if not metadata_path.exists():
            raise ValueError("Staged work is incomplete; rerun stage with --restart.")
        metadata = _stage_metadata(metadata_path)
        if metadata.get("manifest_sha256") != manifest_hash:
            raise ValueError("The source manifest changed; rerun stage with --restart.")
        if metadata.get("refresh_citations") != str(refresh_citations).lower():
            raise ValueError(
                "Citation-refresh mode changed; rerun stage with --restart."
            )
        stage_date = metadata["stage_date"]
    else:
        if not canonical_path.exists():
            raise ValueError(f"Published corpus is missing: {canonical_path}")
        work_dir.mkdir(parents=True, exist_ok=True)
        stage_date = today_iso()
        write_csv_rows(
            metadata_path,
            [
                {"key": "stage_date", "value": stage_date},
                {"key": "manifest_sha256", "value": manifest_hash},
                {
                    "key": "refresh_citations",
                    "value": str(refresh_citations).lower(),
                },
            ],
            ["key", "value"],
        )
        build(
            work_dir,
            manifest_path,
            existing_path=canonical_path,
            audit_dir=work_dir,
            qa_dir=work_dir,
        )

    active_environment = environment if environment is not None else os.environ
    load_env_file(project / ".env.local", active_environment)
    if not skip_openalex:
        api_key = active_environment.get("OPENALEX_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "OPENALEX_API_KEY is missing. Add it to .env.local or use "
                "--skip-openalex."
            )
        enrich_openalex(
            candidate_path,
            candidate_path,
            api_key,
            refresh_citations,
            0.1,
            refresh_date=stage_date if refresh_citations else None,
        )
    if not skip_semantic_scholar:
        enrich_semantic_scholar(
            candidate_path,
            candidate_path,
            active_environment.get("S2_API_KEY", "").strip(),
            refresh_citations,
            2.0,
            refresh_date=stage_date if refresh_citations else None,
        )

    ledger_path = curation_dir / "taxonomy_v0_3_expert_decisions.csv"
    reviewed_ids = (
        {row["record_id"] for row in read_csv_rows(ledger_path)}
        if ledger_path.exists()
        else set()
    )
    classify(
        candidate_path,
        candidate_path,
        taxonomy_path,
        work_dir / "taxonomy_review_queue.csv",
        pending_only=True,
        reviewed_ids=reviewed_ids,
    )
    papers = read_csv_rows(candidate_path)
    review_path = work_dir / "google_scholar_review.csv"
    if resuming and review_path.exists():
        prior_review = read_csv_rows(review_path)
        scholar_rows = google_scholar_review_rows(papers, prior_review, refresh=False)
    else:
        prior_snapshot_path = data_dir / "google_scholar_citations.csv"
        prior_snapshot = (
            read_csv_rows(prior_snapshot_path)
            if prior_snapshot_path.exists() and prior_snapshot_path.stat().st_size
            else []
        )
        scholar_rows = google_scholar_review_rows(
            papers,
            prior_snapshot,
            refresh=refresh_citations,
        )
    write_csv_rows(review_path, scholar_rows, SCHOLAR_REVIEW_FIELDS)
    validate(
        candidate_path,
        work_dir / "qa",
        False,
        manifest_path,
    )


def finalize_release(project: Path, scholar_limit: int = 50) -> None:
    from apply_manual_overrides import apply_overrides
    from apply_taxonomy_reviews import apply_taxonomy_reviews
    from build_3dv_corpus import load_edition_registry
    from make_atlas_tables import make_tables
    from record_snapshot import record as record_snapshot
    from validate_corpus import validate

    project = Path(project).resolve()
    data_dir = project / "data"
    curation_dir = data_dir / "curation"
    work_dir = project / ".work"
    candidate_path = work_dir / "3dv_papers.csv"
    review_path = work_dir / "google_scholar_review.csv"
    manifest_path = curation_dir / "source_manifest.csv"
    taxonomy_path = project / "config/topic_taxonomy.json"
    if not candidate_path.exists():
        raise ValueError("No staged candidate exists. Run the stage command first.")
    if not review_path.exists():
        raise ValueError("The staged Google Scholar review CSV is missing.")

    finalizing_path = work_dir / "finalizing.csv"
    shutil.copy2(candidate_path, finalizing_path)
    try:
        overrides_path = curation_dir / "manual_overrides.csv"
        if overrides_path.exists():
            apply_overrides(finalizing_path, overrides_path, finalizing_path)
        apply_taxonomy_reviews(
            finalizing_path,
            curation_dir / "taxonomy_v0_3_expert_decisions.csv",
            taxonomy_path,
            finalizing_path,
        )
        papers = read_csv_rows(finalizing_path)
        review_rows = read_csv_rows(review_path)
        scholar_snapshot = validate_google_scholar_review(
            papers, review_rows, scholar_limit
        )
        papers = apply_google_scholar_review(papers, scholar_snapshot)
        write_csv_rows(finalizing_path, papers, list(papers[0]))

        registry = load_edition_registry(manifest_path)
        validate_conference_statistics(
            read_csv_rows(data_dir / "conference_statistics_by_year.csv"),
            registry,
        )

        qa_dir = work_dir / "qa"
        atlas_dir = work_dir / "atlas"
        if qa_dir.exists():
            shutil.rmtree(qa_dir)
        if atlas_dir.exists():
            shutil.rmtree(atlas_dir)
        make_tables(finalizing_path, atlas_dir, taxonomy_path)
        published_path = work_dir / "published.csv"
        published_rows = published_paper_rows(read_csv_rows(finalizing_path))
        write_csv_rows(published_path, published_rows, list(published_rows[0]))
        result = validate(published_path, qa_dir, True, manifest_path)
        if result:
            raise ValueError("Strict validation failed for the staged release.")

        scholar_staged = work_dir / "google_scholar_citations.csv"
        write_csv_rows(
            scholar_staged,
            scholar_snapshot,
            SCHOLAR_SNAPSHOT_FIELDS,
        )
        snapshot_log = curation_dir / "citation_snapshot_log.csv"
        snapshot_log_staged = work_dir / "citation_snapshot_log.csv"
        if snapshot_log.exists():
            shutil.copy2(snapshot_log, snapshot_log_staged)
        record_snapshot(
            published_path,
            snapshot_log_staged,
            "Google Scholar",
            "Manual Google Scholar website review of the OpenAlex/Semantic "
            "Scholar top-50 union; no third-party Scholar API used.",
        )
        metadata_path = work_dir / "stage_metadata.csv"
        if metadata_path.exists():
            metadata = _stage_metadata(metadata_path)
            if metadata.get("refresh_citations") == "true":
                record_snapshot(
                    published_path,
                    snapshot_log_staged,
                    "OpenAlex",
                    "Annual resumable citation refresh.",
                )
                record_snapshot(
                    published_path,
                    snapshot_log_staged,
                    "Semantic Scholar",
                    "Annual DOI-batch comparison refresh.",
                )
        publish_pairs: list[tuple[Path, Path]] = [
            (scholar_staged, data_dir / "google_scholar_citations.csv"),
            (snapshot_log_staged, snapshot_log),
            (
                work_dir / "paper_counts_by_year.csv",
                data_dir / "paper_counts_by_year.csv",
            ),
        ]
        publish_pairs.extend(
            (path, project / "outputs/qa" / path.name)
            for path in sorted(qa_dir.glob("*.csv"))
        )
        publish_pairs.extend(
            (path, project / "outputs/atlas" / path.name)
            for path in sorted(atlas_dir.glob("*.csv"))
        )
        for name in ("excluded_records.csv", "supplemental_match_audit.csv"):
            source = work_dir / name
            if source.exists():
                publish_pairs.append((source, curation_dir / name))
        for name in ("build_coverage.csv", "update_diff.csv"):
            source = work_dir / name
            if source.exists():
                publish_pairs.append((source, project / "outputs/qa" / name))

        for source, destination in publish_pairs:
            _atomic_copy(source, destination)
        canonical_path = data_dir / "3dv_papers.csv"
        _atomic_copy(published_path, canonical_path)
    except Exception:
        finalizing_path.unlink(missing_ok=True)
        raise
    shutil.rmtree(work_dir)


def validate_current_release(project: Path, scholar_limit: int = 50) -> None:
    from apply_taxonomy_reviews import apply_taxonomy_reviews
    from build_3dv_corpus import load_edition_registry
    from validate_corpus import validate

    project = Path(project).resolve()
    data_dir = project / "data"
    curation_dir = data_dir / "curation"
    canonical_path = data_dir / "3dv_papers.csv"
    manifest_path = curation_dir / "source_manifest.csv"
    papers = read_csv_rows(canonical_path)
    registry = load_edition_registry(manifest_path)
    validate_conference_statistics(
        read_csv_rows(data_dir / "conference_statistics_by_year.csv"), registry
    )
    scholar_snapshot = validate_google_scholar_review(
        papers,
        read_csv_rows(data_dir / "google_scholar_citations.csv"),
        scholar_limit,
    )
    validate_google_scholar_replay(papers, scholar_snapshot)
    with tempfile.TemporaryDirectory() as tmp:
        reviewed = Path(tmp) / "reviewed.csv"
        shutil.copy2(canonical_path, reviewed)
        apply_taxonomy_reviews(
            reviewed,
            curation_dir / "taxonomy_v0_3_expert_decisions.csv",
            project / "config/topic_taxonomy.json",
            reviewed,
        )
        validate_taxonomy_replay(papers, read_csv_rows(reviewed))
    result = validate(
        canonical_path,
        project / "outputs/qa",
        True,
        manifest_path,
    )
    if result:
        raise ValueError("The published release failed strict validation.")
