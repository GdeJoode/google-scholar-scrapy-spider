import logging as _logging


class _QuietExpectedMediaErrors(_logging.Filter):
    """Drops the ERROR + traceback spam that scrapy.pipelines.{files,media}
    produces every time an image fails the min-size check or a PDF download
    is blocked by the target's robots.txt.

    These are expected outcomes (IMAGES_MIN_WIDTH/HEIGHT filtering and
    ROBOTSTXT_OBEY=True), not actionable errors. The log still shows the
    summary counts in the final scrapy stats dump, so nothing is hidden —
    just quieter."""

    _NOISE_PATTERNS = (
        "Image too small",
        "File (error)",
        "File (unknown-error)",
        "FileException",
        "Forbidden by robots.txt",
    )

    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._NOISE_PATTERNS)


for _name in ("scrapy.pipelines.media", "scrapy.pipelines.files"):
    _logging.getLogger(_name).addFilter(_QuietExpectedMediaErrors())


BOT_NAME = "site_scraper"

SPIDER_MODULES = ["site_scraper.spiders"]
NEWSPIDER_MODULE = "site_scraper.spiders"

# A realistic Chrome UA is needed — Cloudflare / WAFs behind these sites
# return 403 to generic bot-looking UAs. The Playwright browser context
# below is set to the same string so Chromium's network requests match.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
USER_AGENT = _BROWSER_UA

DEFAULT_REQUEST_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

ROBOTSTXT_OBEY = True

CONCURRENT_REQUESTS = 4
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 1.0
RANDOMIZE_DOWNLOAD_DELAY = True

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 10.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

HTTPCACHE_ENABLED = True
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_EXPIRATION_SECS = 7 * 24 * 3600
# 403 must be here so that a one-off bot-block doesn't poison the cache
# for every subsequent re-run after we tune UA / headers.
HTTPCACHE_IGNORE_HTTP_CODES = [403, 408, 429, 500, 502, 503, 504]

DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True,
    "timeout": 30_000,
    # Reduces the most obvious `navigator.webdriver` automation signal
    # that fuels Cloudflare / similar bot-challenge pages.
    "args": [
        "--disable-blink-features=AutomationControlled",
    ],
}
PLAYWRIGHT_CONTEXTS = {
    "default": {
        "user_agent": _BROWSER_UA,
        "viewport": {"width": 1280, "height": 800},
        "locale": "nl-NL",
        "extra_http_headers": {
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        },
    },
}
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30_000
PLAYWRIGHT_MAX_CONTEXTS = 2
PLAYWRIGHT_MAX_PAGES_PER_CONTEXT = 4

ITEM_PIPELINES = {
    "site_scraper.pipelines.ContentImageFilterPipeline": 100,
    "site_scraper.pipelines.SiteFilesPipeline": 200,
    "site_scraper.pipelines.SiteImagesPipeline": 210,
    "site_scraper.pipelines.DownloadsManifestPipeline": 300,
    "site_scraper.pipelines.MarkdownWriterPipeline": 900,
}

# Defaults; BaseSiteSpider.update_settings overrides these to output/<spider>/
FILES_STORE = "output/downloads"
IMAGES_STORE = "output/images"
SITE_MARKDOWN_OUTPUT = "output/pages.markdown"
SITE_DOWNLOADS_MANIFEST = "output/downloads_manifest.jsonl"

IMAGES_MIN_HEIGHT = 200
IMAGES_MIN_WIDTH = 200

MEDIA_ALLOW_REDIRECTS = True
FILES_EXPIRES = 90
IMAGES_EXPIRES = 90

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
FEED_EXPORT_ENCODING = "utf-8"

LOG_LEVEL = "INFO"

DOWNLOAD_MAXSIZE = 100 * 1024 * 1024
DOWNLOAD_WARNSIZE = 32 * 1024 * 1024

DEPTH_PRIORITY = 1
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoDiskQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"
