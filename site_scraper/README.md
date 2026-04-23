# Site scraper

Scrapy + Playwright project that crawls a site, extracts every page as
plain text and markdown, downloads content images and document files
(PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, CSV, ZIP), and rewrites the
generated markdown so it references the locally downloaded files.

## Spiders

| Spider          | Root site                       | Off-site | Other constraints |
| --------------- | ------------------------------- | -------- | ----------------- |
| `oecd`          | `https://oecd.ai/en/`           | no       | Only `/en/` paths; skips `/en/data` |
| `agendastad`    | `https://agendastad.nl/`        | yes      | Follows each city-deal's own site (one domain deep) |
| `elkeregiotelt` | `https://www.elkeregiotelt.nl/` | yes      | Follows each regiodeal / NPVR site (one domain deep) |

### What "one domain deep" means

When the root site links out to an external site X (for example a
city-deal's own website), the spider will:

- fetch that external page, and
- keep crawling **within X** just like it would on the root site.

But the spider never hops further: if a page on X links out to yet
another domain Y, that link is **not** followed. Instead the URL is
stored on that page's item (as `external_links` in
`pages.jsonl`, and listed at the bottom of the page section in
`pages.markdown`) so you can review later and decide whether Y is
worth crawling separately.

This keeps the crawl bounded — at most "root site" + "one external
site per original link" — while still surfacing every onward link you
might want to reconsider.

## Output layout

Each spider writes to its own directory, relative to the working
directory it runs in:

```
output/<spider-name>/
├── pages.jsonl                # one JSON object per page
├── publications.jsonl         # one JSON object per PDF found via inline-scan or search
├── downloads_manifest.jsonl   # one JSON object per unique downloaded file
├── pages.markdown             # all pages + publications concatenated as markdown
├── images/                    # downloaded images, hash-named
└── downloads/                 # downloaded documents, <slug>_<hash>.<ext>
```

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
    allow_off_site = False          # or True for 1-hop off-site
    # allowed_path_prefix = "/en/"  # optional
    # deny_path_prefixes = ("/data",)  # optional
```

No other changes required — pipelines and per-spider output directories
are inherited automatically.
