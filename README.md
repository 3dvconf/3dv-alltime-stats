# 3DV paper metadata pipeline

This repository maintains the data behind **3DV Through the Years**. It keeps a
canonical paper table, conference-level statistics, citation snapshots, and the
Research Atlas topic classification in one place so a future edition can be
added without rebuilding the project by hand.

The current Research Atlas v0.3 release contains 1,351 papers from 13 held
editions. Every paper has an abstract and a manually reviewed primary topic;
the complete decision ledger is checked in for replay and audit.

## Quick check

The pipeline uses Python 3.11 or newer and the standard library only. From this
folder, run:

```bash
python scripts/run_pipeline.py validate
python -m unittest discover -s tests -v
```

The current release should report 1,351 rows, no validation errors, one
documented OpenAlex warning, and ten provider-review rows.

## First-time local setup

Make a private environment file and add your own credentials:

```bash
cp .env.example .env.local
```

`OPENALEX_API_KEY` is required for a normal annual stage. `S2_API_KEY` is
optional; Semantic Scholar can run unauthenticated and is deliberately paced.
The pipeline loads `.env.local` without printing the values or placing them in
command-line arguments. The file is ignored by Git.

Do not put credentials in a CSV, source file, shell command committed to Git,
or issue report.

## Updating the corpus for a new edition

The annual workflow has three phases: stage, review, and finalize.

### 1. Register the edition

Add one row to `data/curation/source_manifest.csv`. This manifest is the only
edition registry: it defines the DBLP query range, edition numbers, held years,
and explicit no-edition years. Add the official conference statistics to
`data/conference_statistics_by_year.csv` and save a clean proof-page JPG under
`data/conference_statistics_evidence/`.

For conference statistics, transcribe the valid full-paper submission total and
the reported accepted total. Oral + spotlight + poster must equal accepted
papers. Keep one acceptance rate with two decimal places, the oral percentage
when available, and the number of participating reviewers—not a nominal or
invited reviewer pool.

### 2. Build a safe candidate

```bash
python scripts/run_pipeline.py stage
```

This writes an ignored candidate to `.work/3dv_papers.csv`. It never changes the
published `data/3dv_papers.csv`. Fresh DBLP identities are merged with the
existing enrichment, manual corrections, and reviewed taxonomy for unchanged
record IDs. The stage stops if a historical DBLP paper disappears and records
new papers or changed bibliography in `.work/update_diff.csv`.

A normal stage queries OpenAlex and Semantic Scholar only for new or incomplete
rows. To refresh existing provider counts as well, use:

```bash
python scripts/run_pipeline.py stage --refresh-citations
```

Both forms are resumable. Repeating the same command continues the candidate
and does not fetch DBLP again. Use `--restart` only when you intentionally want
to discard the current `.work/` candidate and rebuild it.

### 3. Complete the two manual reviews

Taxonomy review is required only for new or previously unreviewed papers.
Review each paper's complete title **and abstract**, then append one final row to
`data/curation/taxonomy_v0_3_expert_decisions.csv`. Give it one dominant primary
topic, zero to two substantive secondary topics, confidence, reviewer, and
date. `.work/taxonomy_review_queue.csv` is the working list; the deterministic
classifier is only a proposal.

Google Scholar is also manual. Fill every row in
`.work/google_scholar_review.csv` by searching the Google Scholar website and
record either a verified non-negative count or the explicit `not_found` method.
The queue is the exact union of the OpenAlex top 50 and Semantic Scholar top 50;
stable corpus order resolves a tie at a cutoff. Counts are never collected with
a Scholar scraper or a third-party Scholar API because those services proved
too unreliable for this dataset.

### 4. Publish only after every gate passes

```bash
python scripts/run_pipeline.py finalize
```

Finalization checks complete taxonomy and Scholar coverage, unique record IDs
and DOIs, citation provenance, conference-statistics arithmetic, and strict
corpus validation. It regenerates the Atlas and QA tables, publishes the
candidate atomically, updates citation-snapshot provenance idempotently, and
removes `.work/` only after success. If a gate fails, the published corpus stays
untouched and the candidate remains available to correct.

## Refreshing the website

The website importer consumes only the stable `data/` interface. From the
website repository, run:

```bash
npm run data:build -- --source-dir /path/to/3dv_paper_metadata_pipeline/data
```

You may instead set `THREEDV_DATA_DIR` to that data folder. The importer reads
`3dv_papers.csv` and `conference_statistics_by_year.csv`; it does not expose
citation counts in the public paper explorer.

## Where things live

- `data/3dv_papers.csv` is the canonical, published paper table.
- `data/conference_statistics_by_year.csv` is the manually curated edition
  statistics table.
- `data/google_scholar_citations.csv` is the compact manual Scholar snapshot.
- `data/paper_counts_by_year.csv` is the edition-level coverage summary and is
  intentionally retained.
- `data/topic_taxonomy.csv` is the current public taxonomy definition.
- `data/curation/` contains the sole edition manifest, replayable corrections,
  the v0.3 expert ledger, provider snapshot log, and source audits.
- `data/conference_statistics_evidence/` contains the clean proof-page JPGs.
- `outputs/atlas/` contains chart-ready taxonomy tables.
- `outputs/qa/` contains validation reports and the provider-review queue.

See [data/DATA.md](data/DATA.md) for the field-level data map and
[docs/SOURCE_AND_CITATION_POLICY.md](docs/SOURCE_AND_CITATION_POLICY.md) for
source rules. The current classification is defined directly in
`data/topic_taxonomy.csv` and `config/topic_taxonomy.json`.

## Citation policy in brief

DBLP is authoritative for paper identity and bibliography. OpenAlex is the main
automated enrichment source. Semantic Scholar is a separate DOI-based audit;
its counts are not blended into OpenAlex. Google Scholar is a manually checked
snapshot for the two-provider top-50 union and is the default ranking source
only within that reviewed set. Blank provider values mean unavailable or not
reviewed, never zero.

Citation counts are snapshots and will drift. Keep provider name and snapshot
date with every count, and never mix providers in one ranking.

## Credit

Setup and curated by **Yue Li, 2026**.
