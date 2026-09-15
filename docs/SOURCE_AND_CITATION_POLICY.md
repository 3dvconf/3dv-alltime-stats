# Source and citation policy

This policy keeps identity, enrichment, and citation evidence separate. A
missing provider result stays visible; it is never repaired with an unlabelled
guess.

## Edition registry and canonical identity

`data/curation/source_manifest.csv` is the only edition registry. It records
held editions, explicit no-edition years, sequential edition numbers, official
DBLP tables of contents, and optional supplemental sources. Code and tests must
derive year ranges from this file rather than hard-coding a final year.

DBLP is authoritative for record ID, year, edition number, title, authors, DOI,
pages, DBLP key, and persistent URL. The annual stage compares the fresh DBLP
inventory with the published corpus. New and bibliographically changed rows are
reported; unexplained removal of any historical record stops the stage.

Administrative proceedings material is removed by a deterministic filter and
retained in `data/curation/excluded_records.csv` with its reason.

## Abstracts and keywords

Official or source-provided abstracts and keywords take precedence. An optional
supplemental record can match by exact DOI, unique normalized title, or the
documented high-threshold fuzzy title rule. Every attempted supplemental merge
is written to `data/curation/supplemental_match_audit.csv`; it never replaces
DBLP identity.

OpenAlex may fill an abstract only when no source abstract exists. Abstracts
are not generated. Final topic review always uses the complete title and full
available abstract, with source keywords and provider topics as supporting
context.

## OpenAlex

OpenAlex is the primary automated enrichment source. Match by exact DOI first;
if that is unavailable, use title plus publication year and require the
documented similarity threshold. Store the OpenAlex work ID, count, annual
counts, topics, keywords, snapshot date, method, and confidence.

A normal annual stage queries only new or incomplete rows. A deliberate
`--refresh-citations` stage refreshes existing rows resumably. `not_found` and
API-error results remain explicit in the metadata review queue. A verified
fallback may populate `citation_primary_*` with its own provider and date, but
must not populate or masquerade as OpenAlex fields.

`OPENALEX_API_KEY` belongs only in ignored `.env.local` or the process
environment. It must never be committed or printed.

## Semantic Scholar

Semantic Scholar is a separate comparison audit. Resolve papers in batches by
exact normalized DOI (or DBLP key only when DOI is unavailable), retain its
paper ID, counts, fields of study, match status, and snapshot date, and do not
fall back automatically to broad title search.

Requests are capped at 400 identifiers per batch, use retry/backoff, and pause
two seconds between unauthenticated batches. `S2_API_KEY` is optional and, when
used, follows the same local-secret rule as the OpenAlex key.

Semantic Scholar counts are never copied into OpenAlex fields and are never
averaged with another provider.

## Google Scholar

Google Scholar collection is manual. Third-party Scholar APIs proved too
unreliable for title matching, so this repository contains no Scholar scraper
or third-party API integration.

The review set is the exact union of the 50 highest OpenAlex counts and the 50
highest Semantic Scholar counts. Rankings use descending count and stable
canonical corpus order to break a cutoff tie. For every candidate, search the
Google Scholar website, verify title, year, authors, and 3DV context, then record
either:

- a non-negative integer count with `matched_exact_context` or
  `matched_human_verified`; or
- a blank count with the explicit `not_found` method.

The compact result is saved in `data/google_scholar_citations.csv` and replayed
by stable record ID. Papers outside that file have blank Scholar counts; blank
means unreviewed or unavailable, never zero. Google Scholar is the default
ranking source only inside this manually reviewed set.

## Ranking and provenance rules

- Compare providers in separate rankings using
  `google_scholar_citation_count`, `openalex_cited_by_count`, or
  `s2_citation_count`.
- Keep provider name and snapshot date with each reported count.
- Do not add or average counts across providers. Differences commonly reflect
  how preprints, proceedings versions, and duplicates are grouped.
- Treat a labelled fallback such as Crossref separately from provider rankings.
- Refresh counts before publishing time-sensitive claims, then record the
  corpus hash in `data/curation/citation_snapshot_log.csv`.

Finalization validates provider provenance and refuses to publish inconsistent
primary-source counts or dates.
