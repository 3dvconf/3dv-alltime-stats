# Data guide

This folder is the stable published interface for the 3DV metadata pipeline.
Files needed for replay or audit stay in focused subfolders so the top level is
small enough to understand at a glance.

## Published tables

- `3dv_papers.csv` — one canonical row per research paper. It contains DBLP
  identity and bibliography, abstracts and keywords, separate citation-provider
  fields, and the reviewed Research Atlas classification.
- `conference_statistics_by_year.csv` — manually transcribed conference
  logistics for every held edition: valid full-paper submissions, accepted
  papers, oral/spotlight/poster counts, rates, area chairs, and participating
  reviewers.
- `google_scholar_citations.csv` — manual Google Scholar results for the exact
  OpenAlex-top-50 ∪ Semantic-Scholar-top-50 candidate set.
- `paper_counts_by_year.csv` — per-edition paper and metadata coverage counts.
  This is a maintained summary, not a second copy of the corpus.
- `topic_taxonomy.csv` — the current taxonomy definitions and display metadata.
- `data_dictionary.csv` — column definitions for the canonical paper table.

The canonical corpus is deliberately not duplicated under a year-specific or
version-specific filename.

## Curation records

`curation/` contains the inputs needed to reproduce decisions:

- `source_manifest.csv` is the sole registry of held and no-edition years,
  edition numbers, DBLP tables of contents, and optional supplemental sources.
- `manual_overrides.csv` contains field-level corrections replayed before final
  taxonomy decisions.
- `taxonomy_v0_3_expert_decisions.csv` contains exactly one reviewed decision
  per canonical paper.
- `citation_snapshot_log.csv` binds each provider snapshot to a date and corpus
  hash.
- `excluded_records.csv` records proceedings front matter removed from the
  paper inventory.
- `supplemental_match_audit.csv` records every supplemental metadata match or
  non-match without replacing DBLP identity.

Research Atlas v0.3 is the current replayable taxonomy release.

## Evidence and generated outputs

`conference_statistics_evidence/` contains clean JPG renderings of the official
source pages used for the conference-statistics transcription. Filenames begin
with the conference year.

Generated tables live outside this folder:

- `../outputs/atlas/` contains chart-ready taxonomy summaries.
- `../outputs/qa/` contains validation results, coverage, and the explicit
  provider-review queue. Annual taxonomy review happens only in `.work/`.

These outputs can be regenerated; the canonical paper table, conference
statistics, compact Scholar snapshot, manifest, overrides, and expert ledger
are the maintained source records.

## Important interpretation rules

- `record_id` is the stable key. Titles are human-readable identifiers but can
  receive bibliographic corrections.
- DBLP owns canonical identity and bibliography. Supplemental sources enrich
  abstracts and keywords only.
- Citation providers stay separate. A blank citation cell means unavailable or
  outside the reviewed set; it does not mean zero.
- `google_scholar_citation_count` is copied from the compact manual snapshot.
  It is the default ranking field only for rows covered by that snapshot.
- Every paper row keeps the final taxonomy results only: version, primary topic
  and group, zero to two secondary topics, method tags, and domain tags. Review
  confidence and reviewer provenance stay in the separate expert ledger.
- Conference `accepted_full_papers` is the number stated in the official report.
  Oral + spotlight + poster must equal that total. `has_spotlight_category`
  distinguishes a real zero from a year in which spotlight did not exist.
- `participating_reviewers` means reviewers who handled submissions, not a
  broader invited or nominal pool.

Run `python scripts/run_pipeline.py validate` from the repository root before
using a release. The root README documents the workflow; the current taxonomy
is defined in `topic_taxonomy.csv` and `../config/topic_taxonomy.json`.
