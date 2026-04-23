# oecd.ai site scraper

A Scrapy + Playwright spider that crawls `https://oecd.ai/en/`, extracts the
text of every page as plain text and as markdown, downloads content images
and document downloads (PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, CSV, ZIP), and
rewrites the markdown so it references the locally downloaded files.

## What gets scraped

- Any URL under `https://oecd.ai/en/` that is reachable via links.
- **Excluded**: anything under `https://oecd.ai/en/data` (per requirements).
- Pages are rendered with headless Chromium via Playwright so
  React-rendered content is captured.
- Content images are filtered: SVGs, icons, logos, and flag sprites are
  discarded, and images smaller than 200x200 px are dropped by the images
  pipeline.
- Downloads: any `<a href>` whose path ends in a document extension listed
  above.

## Output layout

Relative to the working directory the spider runs in:

```
output/
├── pages.jsonl        # one JSON object per page (metadata, text, markdown, refs)
├── pages.markdown     # all pages concatenated as markdown, with local links
├── images/            # downloaded images, hash-named
└── downloads/         # downloaded documents, <slug>_<hash>.<ext>
```

Inside `pages.markdown`, image and document URLs that matched a downloaded
file have been rewritten to relative paths like `./images/abcd...jpg` or
`./downloads/report_12ab34cd.pdf`, so the markdown renders correctly when
opened from the `output/` directory.

## Installation

```bash
pip install -r oecd_scraper/requirements.txt
playwright install chromium
```

## Running

From the repository root:

```bash
SCRAPY_PROJECT=oecd scrapy crawl oecd
```

To cap the crawl for testing:

```bash
SCRAPY_PROJECT=oecd scrapy crawl oecd -s CLOSESPIDER_PAGECOUNT=25
```

To change the output directory or markdown file name:

```bash
SCRAPY_PROJECT=oecd scrapy crawl oecd \
    -s FILES_STORE=myout/downloads \
    -s IMAGES_STORE=myout/images \
    -s OECD_MARKDOWN_OUTPUT=myout/pages.markdown \
    -s FEEDS='{"myout/pages.jsonl": {"format": "jsonlines"}}'
```

## Netiquette

- `ROBOTSTXT_OBEY = True`
- `DOWNLOAD_DELAY = 1.0` with randomisation and AutoThrottle
- Concurrency capped at 4 total / 2 per domain
- HTTP cache enabled (7-day expiry) so re-runs don't re-hit the site

Please set a contact email for the crawl by editing `USER_AGENT` in
`oecd_scraper/settings.py` before running a full crawl.

## Notes and caveats

- A full crawl can take hours and produce several GB. Start with
  `CLOSESPIDER_PAGECOUNT` to confirm output looks right.
- The crawl stays on `oecd.ai` and only follows paths under `/en/`.
- The HTTP cache lives in `httpcache/` — delete it to force a fresh crawl.
- Playwright's `networkidle` wait has a 20s cap per page; JS-heavy pages
  that keep polling may still be captured with incomplete content.
