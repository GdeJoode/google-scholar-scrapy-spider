# Site scraper

Scrapy + Playwright project that crawls a site, extracts every page as
plain text and markdown, downloads content images and document files
(PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, CSV, ZIP), and rewrites the
generated markdown so it references the locally downloaded files.

## Spiders

| Spider            | Root site                                 | Other constraints |
| ----------------- | ----------------------------------------- | ----------------- |
| `oecd`            | `https://oecd.ai/en/`                     | Only `/en/` paths; skips `/en/data` |
| `agendastad`      | `https://agendastad.nl/`                  | — |
| `elkeregiotelt`   | `https://www.elkeregiotelt.nl/`           | — |
| `digitaleconomy`  | `https://digitaleconomy.stanford.edu/`    | Other Stanford subdomains go to `external_links` |

### Off-site behaviour: automatic landing-page visits + optional full-crawl escalation

Every spider has two layers of off-site behaviour.

**Layer 1 — automatic landing-page visits** (no approval required).
Whenever the root site links to a page on a different domain, the
spider visits that exact URL once: it saves the page text and
markdown, downloads any PDFs referenced on it, and stops there. No
further links on that external page are followed; everything on it
is added to `external_links` for review. This runs on every crawl so
you automatically harvest referenced PDFs without pulling in whole
external sites.

**Layer 2 — optional full-crawl escalation** (opt-in, per domain).
If you decide an external domain is worth crawling recursively, list
it in `approved_off_site.txt` or pass it on the command line. The
spider will then follow same-domain links within that domain the
same way it follows root-site links.

Use the review helper after a crawl to see which external domains
are referenced:

```bash
python -m site_scraper.review_externals elkeregiotelt
```

It aggregates per-domain counts and writes:

- `output/<spider>/external_domains.jsonl` — machine-readable
- `output/<spider>/approved_off_site.txt` — human-editable; every
  domain starts with `# ` (commented out). Remove the `# ` from
  domains you want a full recursive crawl of, then re-run:

```bash
scrapy crawl elkeregiotelt \
    -a off_site_from_file=output/elkeregiotelt/approved_off_site.txt
```

Or pass domains inline:

```bash
scrapy crawl elkeregiotelt -a off_site_domains=platformisor.nl,citydeal-foo.nl
```

Within an approved domain, links to yet another domain are still
only recorded as `external_links` — the bounded-crawl rule keeps
you from accidentally sucking in a third site. Run the review
helper again if you want to escalate another domain.

## Output layout

Each spider writes to its own directory. Downloads and images are
**grouped into subdirectories** so they don't all pile up in one flat
folder — the grouping key depends on the spider:

### agendastad / elkeregiotelt — grouped by origin host

```
output/agendastad/
├── pages.jsonl
├── publications.jsonl
├── downloads_manifest.jsonl
├── pages.markdown
├── downloads/
│   ├── agendastad.nl/              # files first seen on the root site
│   │   └── <slug>_<hash>.pdf
│   ├── platformisor.nl/            # one city-deal's own site
│   │   └── ...
│   ├── citydeal-digitale-stad.nl/
│   │   └── ...
│   └── ...
└── images/
    ├── agendastad.nl/<hash>.jpg
    ├── platformisor.nl/<hash>.jpg
    └── ...
```

One subfolder per domain, so each city-deal's (or regiodeal's)
material stays bundled together.

### oecd — grouped by `/en/<section>/`

Since everything lives on `oecd.ai`, the spider splits by the first
URL segment after `/en/`:

```
output/oecd/
├── pages.jsonl
├── ...
├── downloads/
│   ├── oecd.ai/
│   │   ├── policy-initiatives/
│   │   ├── dashboards/
│   │   ├── community/
│   │   ├── wonk/
│   │   └── ...
└── images/
    └── oecd.ai/
        ├── policy-initiatives/<hash>.jpg
        └── ...
```

The subdirectory is picked from the page URL where the file/image was
first discovered; a PDF seen on multiple pages ends up in the
subdirectory of whichever page found it first (every referrer is
still recorded in `downloads_manifest.jsonl`).

### Customising the grouping

Every spider inherits a `file_group_path(item)` method from
`BaseSiteSpider`; the default returns the host of the page. Override
it in your spider to pick any relative path you like (see
`oecd.py` for an example that looks at URL segments).

Inside `pages.markdown`, image and document URLs that matched a
downloaded file have been rewritten to relative paths like
`./images/abcd...jpg` or `./downloads/report_12ab34cd.pdf`, so the
markdown renders correctly when opened from the spider's output
directory.

Each JSON line in `pages.jsonl` also carries:

- `off_site` (bool) and `off_site_domain` (str) — so you can tell
  root-site pages from pages fetched on an external site, and see
  which external site the crawl was restricted to at that point.
- `external_links` (list of URLs) — links on that page that pointed
  at a further external domain and were **not** followed; review them
  later to decide whether to crawl any of those sites too.

## Publication (PDF) discovery

PDFs are found in three layered steps; each layer is only used when
the previous one turns up nothing for a given page:

1. **Direct links.** Any `<a href>` whose path ends in a document
   extension (PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, CSV, ZIP).
2. **Inline scan.** A regex pass over the full rendered HTML picks
   up document URLs buried in inline JSON, `data-*` attributes, and
   script blobs. This is what lets us bypass email-gated "download"
   buttons when the file itself is publicly hosted on the same site.
3. **Search fallback.** If a page looks like an email-gated
   publication download (form with an email input + keywords like
   "download" / "publicatie" / "rapport" nearby) **and** steps 1–2
   produced nothing, the spider fires **one** search query
   (`"<title>" filetype:pdf`) against DuckDuckGo's HTML endpoint,
   fuzzy-matches the top PDF results against the page's `<h1>` /
   `<title>` using `difflib.SequenceMatcher` (default threshold
   0.55), and fetches the best match.

DuckDuckGo is used instead of Google because Google aggressively
blocks scraped queries with captchas. The engine is pluggable: point
`publication_search_engine` at another implementation in a spider
subclass if you have a Google Custom Search API key and want to wire
that up.

### downloads_manifest.jsonl

Every downloaded file ends up in `downloads_manifest.jsonl` with its
provenance so you can always see where a given PDF came from:

```json
{
  "pdf_url": "https://cdn.example.nl/reports/rapport-2024.pdf",
  "local_path": "rapport-2024_3f2a1b9c.pdf",
  "discovery_methods": ["inline-scan"],
  "referrers": ["https://www.platformisor.nl/publicaties/rapport-2024/"],
  "publication_title": "Rapport 2024",
  "search_engine": "",
  "search_query": "",
  "fuzzy_score": null,
  "status": "downloaded",
  "checksum": "..."
}
```

For search-fallback finds the entry also includes the search query,
engine, and fuzzy score so you can sanity-check questionable matches.

If a publication search didn't produce a good enough fuzzy match, no
PDF is downloaded and the spider logs a line like:

```
No fuzzy match for publication 'Rapport 2024' (best score=0.41, candidates=7)
```

so you can grep those out of the log and decide manually.

## Installation

```bash
pip install -r site_scraper/requirements.txt
playwright install chromium
```

### Running on Google Colab

A ready-to-run notebook is checked in at the repo root:
[`run_scraper.ipynb`](../run_scraper.ipynb). Open it directly in Colab via

```
https://colab.research.google.com/github/GdeJoode/google-scholar-scrapy-spider/blob/claude/build-website-scraper-AOTSB/run_scraper.ipynb
```

`Runtime` → `Run all` installs Playwright, checks out the feature
branch, does a 25-page test crawl, and (optionally) persists output
to Google Drive.

## Running

From the repository root:

```bash
SCRAPY_PROJECT=site scrapy crawl oecd
SCRAPY_PROJECT=site scrapy crawl agendastad
SCRAPY_PROJECT=site scrapy crawl elkeregiotelt
```

To cap a crawl for testing:

```bash
SCRAPY_PROJECT=site scrapy crawl agendastad -s CLOSESPIDER_PAGECOUNT=25
```

To override output paths for a single run:

```bash
SCRAPY_PROJECT=site scrapy crawl oecd \
    -s FILES_STORE=myout/downloads \
    -s IMAGES_STORE=myout/images \
    -s SITE_MARKDOWN_OUTPUT=myout/pages.markdown \
    -s FEEDS='{"myout/pages.jsonl": {"format": "jsonlines"}}'
```

## Netiquette

- `ROBOTSTXT_OBEY = True` — honoured per domain, including the
  external one-hop targets.
- `DOWNLOAD_DELAY = 1.0` with randomisation and AutoThrottle.
- Concurrency capped at 4 total / 2 per domain.
- HTTP cache enabled (7-day expiry) so re-runs don't re-hit the site.

Please set a contact email for the crawl by editing `USER_AGENT` in
`site_scraper/settings.py` before running a full crawl.

## Notes and caveats

- A full crawl can take hours and produce several GB. Start with
  `CLOSESPIDER_PAGECOUNT` to confirm output looks right.
- The HTTP cache lives in `httpcache/` — delete it to force a fresh
  crawl.
- Playwright's `networkidle` wait has a 20s cap per page; JS-heavy
  pages that keep polling may still be captured with incomplete
  content.
- External domains are only entered via a link on the root site. A
  third domain discovered from within an external site is never
  fetched — it is recorded in `external_links` instead so you can
  review and possibly crawl it in a separate run.
- Host matching ignores a leading `www.`, so `example.nl` and
  `www.example.nl` are treated as the same site.

## Adding another site

Drop a new file in `site_scraper/spiders/` subclassing
`BaseSiteSpider`:

```python
from site_scraper.base import BaseSiteSpider

class ExampleSpider(BaseSiteSpider):
    name = "example"
    start_urls = ["https://example.org/"]
    root_domains = {"example.org"}
    # allowed_path_prefix = "/en/"        # optional
    # deny_path_prefixes = ("/data",)     # optional
    # allowed_off_site_domains = {"x.nl"}  # optional; usually overridden via CLI
```

No other changes required — pipelines and per-spider output directories
are inherited automatically.

## Summarising downloaded documents

After a crawl, every PDF / PPTX in `output/<spider>/downloads/` can be
turned into a structured markdown summary with a RAPTOR-style
hierarchical summary tree and a TreeKG-style topic tree.

The pipeline:

1. **Parse** with [docling](https://github.com/DS4SD/docling) — handles
   PDF + PPTX, OCR for scanned pages.
2. **Extract metadata + topic tree** by prompting an LLM on the first
   ~8k characters: title, authors, publication year/month,
   organisation, and a 3–7 main-topic / subtopics tree.
3. **RAPTOR** — chunk the text, embed locally with
   `sentence-transformers/all-MiniLM-L6-v2`, cluster with a Gaussian
   mixture, recursively summarise clusters until a single root summary
   remains.
4. **Write** `output/<spider>/summaries/<original-relpath>.md` with
   YAML frontmatter and the summary tree + topic tree as body.
5. **Build the overview table** that aggregates all frontmatters into
   `output/<spider>/summaries/_overview.md`.

LLM: NVIDIA Build by default (free tier on
[build.nvidia.com](https://build.nvidia.com), OpenAI-compatible API).
Default model: `mistralai/mistral-medium-3-instruct`. Override with
`--model` and/or `--llm-base-url` for any other OpenAI-compatible
provider.

```bash
# Install once
pip install -r site_scraper/requirements-summarize.txt

# Set your NVIDIA Build key
export NVIDIA_API_KEY=nvapi-...

# Run on one spider's downloads (use --limit for a smoke test)
python -m site_scraper.summarize_documents elkeregiotelt --limit 3

# Build the overview table
python -m site_scraper.build_overview elkeregiotelt
```

Each summary file looks like:

```markdown
---
title: "Rapport X"
authors:
  - "Doe, Jane"
publication_year: 2024
publication_month: 3
organisation: "Some Org"
topics:
  - main: "AI governance"
    subtopics: ["risk frameworks", "EU AI Act"]
  - main: "Data sharing"
    subtopics: ["interoperability"]
source_pdf: "https://.../rapport-x.pdf"
local_pdf: "downloads/.../rapport-x.pdf"
summarized_at: "2026-04-23T12:34:56+00:00"
llm_model: "mistralai/mistral-medium-3-instruct"
---

# Rapport X

## Overall summary
…

## Level 1 cluster summaries
### Cluster 1
…

## Topic tree
- **AI governance**
  - risk frameworks
  - EU AI Act
- **Data sharing**
  - interoperability
```

The overview table (`_overview.md`) lists every summarised document
with its title, date, organisation, topics and links to both the
summary file and the source PDF — handy as the index for an obsidian
/ similar vault.

Re-running the summariser is incremental: documents that already have
a `.md` output are skipped unless you pass `--force`.
