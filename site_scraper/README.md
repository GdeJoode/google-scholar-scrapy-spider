# Site scraper

Scrapy + Playwright project that crawls a site, extracts every page as
plain text and markdown, downloads content images and document files
(PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, CSV, ZIP), and rewrites the
generated markdown so it references the locally downloaded files.

## Spiders

| Spider          | Root site                       | Off-site hops | Other constraints |
| --------------- | ------------------------------- | ------------- | ----------------- |
| `oecd`          | `https://oecd.ai/en/`           | 0             | Only `/en/` paths; skips `/en/data` |
| `agendastad`    | `https://agendastad.nl/`        | 1             | Follows city-deal sites one page deep |
| `elkeregiotelt` | `https://www.elkeregiotelt.nl/` | 1             | Follows regiodeal / NPVR sites one page deep |

"Off-site hops = 1" means: when the root site links out (e.g. a
city-deal's own website), the linked page is fetched and saved, but
links on that external page are **not** followed.

## Output layout

Each spider writes to its own directory, relative to the working
directory it runs in:

```
output/<spider-name>/
├── pages.jsonl        # one JSON object per page (metadata, text, markdown, refs)
├── pages.markdown     # all pages concatenated as markdown, with local links
├── images/            # downloaded images, hash-named
└── downloads/         # downloaded documents, <slug>_<hash>.<ext>
```

Inside `pages.markdown`, image and document URLs that matched a
downloaded file have been rewritten to relative paths like
`./images/abcd...jpg` or `./downloads/report_12ab34cd.pdf`, so the
markdown renders correctly when opened from the spider's output
directory.

Each JSON line in `pages.jsonl` also carries an `off_site` flag so you
can tell root-site pages from one-hop external pages.

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
- For `agendastad` / `elkeregiotelt` the one-hop rule only applies to
  links that appear on the root domain. An external page discovered
  indirectly (e.g. via another external page) is never fetched.

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
