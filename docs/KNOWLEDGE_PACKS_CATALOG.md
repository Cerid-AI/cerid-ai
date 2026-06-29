# Knowledge Packs Catalog

> Curator-published, optional baseline corpora for the Cerid AI personal
> knowledge companion. The repo ships **slim** — none of these packs are
> bundled in the install. Operators opt into specific packs through the
> Library UI, REST endpoint, MCP tool, or `scripts/install_knowledge_pack`.

## Why this exists

A fresh Cerid install starts with an empty knowledge base. That's the
right default — the system is privacy-first and your own data should
seed your own KB. But for many domains, a thin shared baseline is
useful: language docs you'll reference once a week, public-domain
financial-literacy primers, classic productivity essays. The
**knowledge-pack harness** (Phases 1–9, landed 2026-05-10) lets users
opt into curated baseline corpora without forcing them on everyone.

**As of v1.0.1 (2026-05-10):** 17 of 23 catalog packs are built,
sha256-pinned, and published as GitHub releases at
[github.com/Cerid-AI/cerid-ai-knowledge-packs](https://github.com/Cerid-AI/cerid-ai-knowledge-packs/releases).
End users install via `pkb_knowledge_pack_install <id>` (MCP),
`POST /knowledge_packs/{id}/install` (REST), the Library UI button,
or `scripts/install_knowledge_pack install <id>` (CLI) — no side-car
configuration needed. Six packs (`bogleheads-wiki`, `pes2o-cs-recent`,
`medlineplus-health-topics`, `caselaw-scotus`, `medquad-health-qa`,
`drug-supplement-key-facts`) remain `planned`/`experimental` pending
adapter follow-up; see the Catalog section below for status per pack.

Every pack in this catalog meets five hard requirements:

1. **Permissive license** — public domain, CC0, CC-BY*, MIT, Apache-2.0,
   BSD, PSF-2.0, or Blue-Oak. Anything copyleft beyond CC-BY-SA is
   rejected. Anything proprietary is rejected outright.
2. **No personal-data exposure** — packs are sourced from authoritative
   third-party publishers (US gov, Wikimedia, Mozilla, Hugging Face,
   etc.) or LLM-prepped synthetic corpora. There is no scraping of
   private fora, no archived Reddit, no email dumps.
3. **RAG-ready** — clean prose (no OCR), semantically self-contained
   sections, structured metadata where possible.
4. **Sub-500 MB per pack** — large enough to be useful, small enough
   that an install completes in minutes on a residential connection.
5. **Complete for its claimed scope** — a pack must contain *all* of
   what its name and description claim. When the sub-500 MB budget can't
   hold a full corpus, narrow the *claim* (scope to a sub-domain, era,
   or curated set) and name it accordingly — **never** ship a silent
   row-sample that answers confidently from a partial corpus. A pack
   that drops part of its claimed scope (e.g. a license-excluded subset)
   must say so in its name/description.

## Catalog

Status legend:

- `built` — tarball published, `download_url` resolvable, ready to install
- `planned` — recipe defined, awaiting curator-build (Phase 7)
- `experimental` — recipe defined but flagged for caveats (license
  edge case, source rate limit, content review pending)

### Coding (9 packs — 9 built)

| ID | Status | License | Size | Files | Source |
|---|---|---|---|---|---|
| `mdn-web-docs` | built v1.0.0 | CC-BY-SA 2.5 prose / CC0 code | 12.8 MB | 14375 | [mdn/content](https://github.com/mdn/content) |
| `python-stdlib-docs` | built v1.0.1 | PSF-2.0 + 0BSD | 163 KB | 208 | [docs.python.org/3/archives/python-3.14-docs-html.zip](https://docs.python.org/3/archives/python-3.14-docs-html.zip) |
| `rust-book` | built v1.0.0 | MIT OR Apache-2.0 | 352 KB | 109 | [rust-lang/book](https://github.com/rust-lang/book) |
| `typescript-handbook` | built v1.0.0 | MIT | 557 KB | 133 | [microsoft/TypeScript-Website](https://github.com/microsoft/TypeScript-Website) |
| `kubernetes-website` | built v1.0.0 | CC-BY-4.0 | 2.1 MB | 1289 | [kubernetes/website](https://github.com/kubernetes/website) |
| `helm-docs` | built v1.0.1 | MIT | 97 KB | 96 | [helm/helm-www](https://github.com/helm/helm-www) |
| `tldr-pages` | built v1.0.0 | CC-BY-4.0 | 1.3 MB | 7102 | [tldr-pages/tldr](https://github.com/tldr-pages/tldr) |
| `apache-spark-docs` | built v1.0.0 | Apache-2.0 | 685 KB | 236 | [apache/spark](https://github.com/apache/spark) |
| `learnxinyminutes` | built v1.0.1 | CC-BY-SA-3.0 | 877 KB | 197 | [adambard/learnxinyminutes-docs](https://github.com/adambard/learnxinyminutes-docs) |

**Why these:**

- **MDN Web Docs** is the single best permissively-licensed web/JS
  reference corpus. Daily updates by Mozilla staff, ~14k pages of
  clean markdown with YAML frontmatter (slug, page-type, browser-compat)
  that maps cleanly to Cerid chunk metadata.
- **Python stdlib docs** authoritative, version-tagged, dual-licensed
  PSF-2.0 (prose) and 0BSD (code samples since 3.8.6). Pre-rendered
  HTML download avoids the reST-to-markdown conversion overhead.
- **Rust Book + std reference** — canonical learning corpus,
  mdbook-format markdown, semantically self-contained sections.
- **TypeScript Handbook** — small, hand-curated, MIT-licensed.

**Skipped:** `the-stack-v2` (32 TB unfiltered, license-per-file
complexity); Stack Overflow archive (post-2024 dump cadence is irregular
and CC-BY-SA attribution per chunk is operationally awkward).

### Finance (3 packs — 2 built, 1 deferred)

| ID | Status | License | Size | Files | Source |
|---|---|---|---|---|---|
| `bogleheads-wiki` | **deferred** | CC-BY-SA 4.0 | — | — | [bogleheads.org/wiki](https://www.bogleheads.org/wiki/Main_Page) |
| `irs-publications-curated` | built v1.0.1 | US gov public domain (CC0) | 17 KB | 7 | [irs.gov/publications](https://www.irs.gov/publications) |
| `cfpb-ask` | built v1.0.0 | US gov public domain (CC0) | 472 KB | 676 | [consumerfinance.gov/ask-cfpb](https://www.consumerfinance.gov/ask-cfpb/) |

**Bogleheads deferral note:** the wiki sits behind a Cloudflare
anti-bot challenge on `/w/api.php` that requires JS execution to
clear; the current `mediawiki_api` adapter can't bypass it. Path
forward is either (a) a Playwright-based adapter that drives a
real browser, or (b) a curator-side manual MediaWiki
`Special:Export` flow paired with a future `wiki_xml_export`
adapter. Tracked in Phase 10 below.

**Why these:**

- **Bogleheads wiki** — the best permissively-licensed personal-finance
  corpus in existence. Practitioner-authored, principles-oriented (so it
  doesn't rot tax-year-by-tax-year), ~1000 pages, deeply cross-linked.
  Acquisition: MediaWiki `Special:Export` against the `Personal_finance`
  + `Investing` category trees.
- **IRS publications (curated)** — Title 17 §105 places these in the
  public domain. The `browser-friendly` HTML build at
  `https://www.irs.gov/forms-pubs/browser-friendly` is RAG-cleaner than
  the PDFs. Ship a tax-year-stamped subset (Pub 17, 463, 502, 525, 590-A,
  590-B, 936) rather than the full publication library.
- **CFPB Ask CFPB** — federal agency Q&A, public domain. Plain-language
  question/answer format is *ideal* RAG shape. ~1500 entries via the
  consumerfinance.gov sitemap.

**Skipped:** Tax-year-dated content as primary (use principles content
first, tax tables as secondary); investing-blog scrapes (license risk).

### Projects (2 packs — 2 built)

| ID | Status | License | Size | Files | Source |
|---|---|---|---|---|---|
| `18f-methods-guides` | built v1.0.0 | CC0 1.0 | 25 KB | 38 | [18F/methods](https://github.com/18F/methods) |
| `chaoss-metrics` | built v1.0.0 | MIT | 4 KB | 4 | [chaoss/metrics](https://github.com/chaoss/metrics) |

**Why these:**

- **18F Methods + Guides** — US GSA TTS (federal government) playbook,
  CC0 (no attribution required). Pre-chunked as method cards.
- **CHAOSS metrics** — Linux Foundation working group, MIT, one
  metric per file. Slot under `projects/general` for community-health
  / project-evolution context.

**Skipped:** Atlassian Agile Coach (proprietary), PMI BoK / PRINCE2
(Axelos proprietary), wikiHow (restrictive ToS).

### Personal (5 packs — 2 built, 3 experimental)

| ID | Status | License | Size | Files | Source |
|---|---|---|---|---|---|
| `wikivoyage-en` | built v1.0.0 | CC-BY-SA 3.0 | 72 MB | 30919 | [enwikivoyage dump](https://dumps.wikimedia.org/enwikivoyage/latest/) |
| `medlineplus-health-topics` | **deferred** (experimental) | US gov public domain (filtered) | — | — | [medlineplus.gov/xml.html](https://medlineplus.gov/xml.html) |
| `medquad-health-qa` | **experimental** | CC-BY-4.0 | — | — | [abachaa/MedQuAD](https://github.com/abachaa/MedQuAD) (drugs/supplements excluded) |
| `drug-supplement-key-facts` | **experimental** | US gov public domain | — | — | [DailyMed](https://dailymed.nlm.nih.gov/dailymed/) + [NIH ODS](https://ods.od.nih.gov/) |
| `gutenberg-classics-curated` | built v1.0.0 | Public domain (CC0) | 3.9 MB | 15 | [gutenberg.org](https://www.gutenberg.org/) |

**MedQuAD scope note:** complete for the **9 redistributable** NLM
subsets; the 3 MedlinePlus subsets (A.D.A.M., drugs, herbs/supplements)
are excluded for copyright — so this pack deliberately does **not** cover
drug/supplement questions. Named to say so rather than imply full
medical coverage.

**MedlinePlus deferral note:** the dump bundles federal-PD content
**plus** copyrighted A.D.A.M. Medical Encyclopedia + drug-monograph
entries. Build gate must filter the latter before sealing. Tracked
in Phase 10.

**Why these:**

- **Wikivoyage** — full English dump, CC-BY-SA, twice-monthly cadence.
  Slot: `personal/travel`.
- **MedlinePlus** — NIH/NLM. Marked `experimental` because the dump
  contains both public-domain federal content **and** copyrighted
  A.D.A.M. Medical Encyclopedia + drug monographs. Builder must
  filter A.D.A.M. + drug entries before tarball seal. Slot:
  `personal/health`. **Build gate: refuse to seal the tarball if any
  A.D.A.M.-prefixed XML element survives the filter.**
- **Gutenberg classics curated** — ~20-30 hand-picked productivity /
  philosophy classics (Marcus Aurelius, Franklin's *Autobiography*,
  Emerson, Thoreau, Bennett's *How to Live on 24 Hours a Day*). Plain
  UTF-8, no OCR risk. Slot: `personal/notes` and as a fallback in
  `general`.

### General (4 packs — 2 built, 2 experimental)

| ID | Status | License | Size | Files | Source |
|---|---|---|---|---|---|
| `wikipedia-simple-en` | built v1.0.1 | CC-BY-SA 3.0 + GFDL | 100 MB | 196034 | [wikimedia/wikipedia 20231101.simple](https://huggingface.co/datasets/wikimedia/wikipedia) |
| `cosmopedia-khanacademy` | built v1.0.1 | Apache-2.0 | 29 MB | 23855 | [HuggingFaceTB/cosmopedia](https://huggingface.co/datasets/HuggingFaceTB/cosmopedia) `khanacademy` config |
| `pes2o-cs-recent` | **deferred** (experimental) | ODC-BY-1.0 | — | — | [allenai/peS2o](https://huggingface.co/datasets/allenai/peS2o) v2 |
| `caselaw-scotus` (sub: `legal`) | **experimental** | CC0-1.0 | — | — | [free-law/Caselaw_Access_Project](https://huggingface.co/datasets/free-law/Caselaw_Access_Project) |

**peS2o deferral note:** uses a deprecated dataset-script loader
(`peS2o.py`); newer `datasets` versions refuse to execute scripts.
Path forward is either (a) wait for an `allenai/peS2o-v2`
non-script mirror, or (b) pin a `datasets<3.0` interpreter
per-recipe. Tracked in Phase 10.

**Why these:**

- **Wikipedia Simple-English** — 242k articles, parquet, ~280 MB.
  Snapshot date is pinned (2023-11-01); refresh with each Wikimedia
  dump cycle. Strongly preferred over the 20 GB full-English corpus
  for personal install.
- **Cosmopedia (khanacademy split)** — Hugging Face's synthetic
  textbook corpus, Apache-2.0. The `khanacademy` config is ~24k rows
  / 72 MB and is the single most practical sub-500 MB
  LLM-prepped synthetic corpus. Drop-in for `general/general`.

- **Caselaw (SCOTUS, `general/legal`)** — the Caselaw Access Project
  went **CC0** in 2024; U.S. Supreme Court opinions are the highest-
  utility, most-cited legal slice. Scoped to the *complete* SCOTUS set
  (not an arbitrary row sample); if it exceeds the budget at build, the
  recipe narrows by era and renames — see completeness (requirement 5).

**Skipped (too large for first-install):** OpenStax full set, full
fineweb-edu, full SlimPajama, full RedPajama, full Cosmopedia
(92 GB / 31M docs).

## License taxonomy

The harness honours four license categories, with progressively more
friction at install time:

| Category | SPDX matches | Default install behavior |
|---|---|---|
| `public_domain` | `CC0-1.0`, `Unlicense`, US-government-PD | Install without prompt |
| `permissive` | `MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `0BSD`, `PSF-2.0`, `Blue-Oak-1.0.0` | Install without prompt |
| `attribution` | `CC-BY-2.5`, `CC-BY-3.0`, `CC-BY-4.0` | Install without prompt; chunks are stamped with attribution metadata at ingest |
| `share_alike` | `CC-BY-SA-2.5`, `CC-BY-SA-3.0`, `CC-BY-SA-4.0`, `GFDL-1.3` | **Requires `--allow-share-alike` (CLI) or explicit toggle (UI)**; embeddings + paraphrases are propagated derivatives, so the user must acknowledge the obligation if their RAG outputs are republished externally |

`NOASSERTION` and missing license fields are rejected at install time
in all configurations.

## PII safeguards

The user explicitly required: *"ensure all packs do not include my
personal data"*. The harness defends this on three layers:

### Build-time (curator-side)

- **Upstream allow-list** at `config/knowledge_packs_allowlist.json` —
  every recipe's `download_url` host + path-prefix must match one of:
  - `github.com/mdn/`, `github.com/python/`, `github.com/rust-lang/`,
    `github.com/microsoft/TypeScript-Website`, `github.com/18F/`,
    `github.com/chaoss/`
  - `huggingface.co/datasets/wikimedia/`,
    `huggingface.co/datasets/HuggingFaceTB/`,
    `huggingface.co/datasets/bigcode/`
  - `dumps.wikimedia.org/`
  - `irs.gov/`, `consumerfinance.gov/`, `medlineplus.gov/`,
    `fdic.gov/`, `cfpb.gov/`, `investor.gov/`, `mymoney.gov/`,
    `federalreserveeducation.org/`
  - `bogleheads.org/`
  - `gutenberg.org/`, `archive.org/details/`
- **PII gate (Phase 8, planned)** — `tools/pii_gate.py` runs Presidio
  with a tightened recognizer set against `content/*` before sealing the
  tarball. Fails if any HIGH-confidence entity is detected. The
  recognizer denylist disables the noisy ones (TFN/PCI Luhn-only).
- **PII gate fast-path** — packs whose entire content is sourced from
  the upstream allow-list are exempt from the Presidio scan (because
  the upstream is itself PII-cleaned). Only user-authored or
  hand-curated content is scanned.

### Install-time (user-side)

- License-category gate (above) — refuse `NOASSERTION`/missing.
- **Provenance metadata at ingest (Phase 8, planned)**: every chunk
  written to chromadb gets `pack:<id>`, `pack-version:<ver>`,
  `source_url`, `source_sha256`, `license_spdx`, `retrieved_at`,
  `recipe_rev`, `adapter`. The audit trail is queryable via the
  existing `/admin/artifacts/{id}` endpoint.
- The current implementation already stamps the first two
  (`pack:<id>` and `pack-version:<ver>`) — see
  `app/services/knowledge_packs.install_pack`.

### Runtime (RAG-side)

- The chunks ingested from a pack flow through the same chromadb +
  Neo4j pipeline as user-authored content. Dedup-by-content-hash
  prevents pack content from silently shadowing user content.
  Quality scoring + entity backfill apply uniformly.
- Pack provenance metadata is filterable in the retrieval layer:
  e.g., a query can be restricted to user-authored content only by
  filtering `NOT EXISTS pack`. (Wired in Phase 8.)

## Implementation strategy

### Phases 1–9 — landed (2026-05-10)

| Phase | What | Surface |
|---|---|---|
| 1–2 | Pure manifest/registry/state model + install/uninstall service + CLI | `core/knowledge/packs.py`, `app/services/knowledge_packs.py`, `scripts/install_knowledge_pack.py` |
| 3 | REST endpoints + 3 MCP tools | `app/routers/knowledge_packs.py`, `app/tools.py` |
| 4 | Web UI dialog with Available / Installed tabs | `src/web/src/components/kb/knowledge-library-dialog.tsx` |
| 5 | Tarball builder for 5 starter packs from in-tree eval corpus + `file://` install support | `scripts/build_knowledge_pack.py` |
| 6 | Catalog doc, registry with 14 planned entries, upstream allow-list, SPDX license-category gate | `config/knowledge_packs.json`, `config/knowledge_packs_allowlist.json` |
| 7 | `PackSourceAdapter` ABC + 7 concrete adapters: `github_zip`, `hf_dataset`, `mediawiki_api`, `html_scrape`, `wiki_dump`, `gutenberg`, `python_docs_zip`. Catalog orchestrator (`scripts/build_catalog`) + 6 sub-phase commits | `core/knowledge/adapter_*.py`, `scripts/build_catalog.py` |
| 8a | Pack-provenance metadata stamped through ingest: `pack_id`, `pack_version`, `pack_license_spdx`, `pack_license_category`, `pack_source_url`, `pack_curator`, `pack_adapter`, `pack_sha256`, `pack_retrieved_at`, `pack_file` | `app/services/ingestion.py`, `app/services/knowledge_packs.py` |
| 8b | Presidio PII gate (opt-in `--pii-scan`) with high-confidence denylist of noisy recognizers | `core/knowledge/pii_gate.py`, `scripts/build_catalog.py` |
| 9 | Catalog expansion +6 packs (kubernetes-website, helm-docs, tldr-pages, apache-spark-docs, learnxinyminutes, peS2o) reusing existing adapters | `config/knowledge_packs.json` |

### Live releases

- [v1.0.0](https://github.com/Cerid-AI/cerid-ai-knowledge-packs/releases/tag/v1.0.0) — 11 packs, ~94 MB total
- [v1.0.1](https://github.com/Cerid-AI/cerid-ai-knowledge-packs/releases/tag/v1.0.1) — +6 packs from recipe/dep fixes, ~130 MB total

### Phase 10 — deferred (new adapters for stretch sources)

Status: **4 of these adapters landed 2026-06-29** — the `hf_dataset`
court-filter, `qa_xml`, `medlineplus_xml`, and `drug_facts` adapters are
implemented + unit-tested, so `caselaw-scotus`, `medquad-health-qa`,
`medlineplus-health-topics`, and `drug-supplement-key-facts` now need only
a **curator build + publish** (run the adapter, seal the tarball, upload
the release, set `download_url` + `sha256`). `bogleheads-wiki` and
`pes2o-cs-recent` remain genuinely blocked on upstream issues.

| Pack | Reason | Adapter |
|---|---|---|
| `bogleheads-wiki` | Cloudflare anti-bot challenge on `/w/api.php` | ⏳ Playwright `mediawiki_browser_api`, **or** operator `Special:Export` + a future `wiki_xml_export` |
| `pes2o-cs-recent` | Deprecated dataset-script loader (`peS2o.py`) refused by current `datasets` | ⏳ wait for non-script `allenai/peS2o-v2`, or pin `datasets<3.0` per-recipe |
| `caselaw-scotus` | Full CAP is 38 GB (CC0); court-filter to the **complete** SCOTUS subset, era-narrow if >500 MB | ✅ **landed** — `hf_dataset` `filter_field`/`filter_value` (no row sampling) |
| `medquad-health-qa` | Ships Question/Answer **XML**, not prose; 3 subsets copyright-stripped | ✅ **landed** — `qa_xml` renders `<QAPair>`, excludes the 3 subsets |
| `medlineplus-health-topics` | NLM topics XML; filter A.D.A.M. + drug/supplement categories | ✅ **landed** — `medlineplus_xml` HTML→prose + category/prefix filter |
| `drug-supplement-key-facts` | Full DailyMed labels are boilerplate-heavy; supplements at NIH ODS | ✅ **landed** — `drug_facts` openFDA key-sections (curated drug list) + NIH ODS supplements |

Other Phase-10 catalog candidates from research (deferred — each
needs a focused 200–300 LOC adapter):

- `jats_xml` — PMC OA Open Access subset (CC0/CC-BY commercial-OK
  split), reusable for any JATS publisher
- `s3_csv_bulk` — CourtListener legal opinions (federal PD)
- `govinfo_bulk` — Federal Register / BILLS / CFR (US-gov-PD daily refresh)
- `pdf_corpus` — IPCC AR6 / NIST CSRC / NTSB CAROL (born-digital PDFs only)
- `cnxml_textbook` — OpenStax (CC-BY-4.0, custom CNXML format)
- `asciidoc_normalizer` — Raspberry Pi documentation

## Curator workflow

For an operator who wants to build + ship a pack today:

```bash
# 1. Author content under data/sources/<id>/content/
mkdir -p data/sources/my-pack/content
# ... author markdown files ...

# 2. Build the tarball
docker exec ai-companion-mcp python -m scripts.build_knowledge_pack custom \
    --pack-id my-pack --version 1.0.0 --domain general \
    --description "..." --license CC0-1.0 \
    --source-dir data/sources/my-pack/content \
    --build-dir data/knowledge-packs/v1

# 3. Side-car registry now points at the new tarball via file://
ls data/knowledge-packs/v1/

# 4. Install end-to-end
docker exec -e CERID_KNOWLEDGE_PACKS_REGISTRY=/workspace/data/knowledge-packs/v1/registry.json \
    ai-companion-mcp python -m scripts.install_knowledge_pack list
```

For Phase 7 / curator-published packs, the workflow extends to:

```bash
# Build all catalog packs from upstream sources (Phase 7)
docker exec ai-companion-mcp python -m scripts.build_catalog --all

# Verify upstream allow-list + license category
docker exec ai-companion-mcp python -m scripts.validate_recipes

# Run PII gate (Phase 8)
docker exec ai-companion-mcp python -m scripts.pii_gate \
    --build-dir data/knowledge-packs/v1

# Upload to GitHub release
gh release upload eval-v1.0.0 data/knowledge-packs/v1/*.tar.gz
```

## Sources cited

Verified against canonical license files and dataset cards:

- [mdn/content LICENSE](https://github.com/mdn/content/blob/main/LICENSE.md)
- [cpython Doc/license.rst](https://github.com/python/cpython/blob/main/Doc/license.rst)
- [rust-lang/book LICENSE](https://github.com/rust-lang/book/blob/main/LICENSE-MIT)
- [microsoft/TypeScript-Website](https://github.com/microsoft/TypeScript-Website)
- [github/CodeSearchNet](https://github.com/github/CodeSearchNet)
- [bigcode/the-stack-v2 dataset card](https://huggingface.co/datasets/bigcode/the-stack-v2)
- [HuggingFaceTB/cosmopedia](https://huggingface.co/datasets/HuggingFaceTB/cosmopedia)
- [HuggingFaceTB/smollm-corpus](https://huggingface.co/datasets/HuggingFaceTB/smollm-corpus)
- [wikimedia/wikipedia](https://huggingface.co/datasets/wikimedia/wikipedia)
- [Wikivoyage dump index](https://dumps.wikimedia.org/enwikivoyage/latest/)
- [MedlinePlus content use](https://medlineplus.gov/about/using/usingcontent/)
- [Bogleheads Wiki Main Page](https://www.bogleheads.org/wiki/Main_Page)
- [IRS Publications](https://www.irs.gov/publications)
- [IRS browser-friendly publications](https://www.irs.gov/forms-pubs/browser-friendly)
- [CFPB Ask CFPB](https://www.consumerfinance.gov/ask-cfpb/)
- [18F/methods](https://github.com/18F/methods)
- [chaoss/metrics](https://github.com/chaoss/metrics)
- [Project Gutenberg](https://www.gutenberg.org/)
- [SPDX 3.0 license list](https://spdx.org/licenses/)
- [Croissant ML metadata spec](https://docs.mlcommons.org/croissant/docs/croissant-spec.html)
- [HF dataset typosquatting study (Internetware 2025)](https://dl.acm.org/doi/10.1145/3755881.3755921)
- [Microsoft Presidio](https://microsoft.github.io/presidio/)
