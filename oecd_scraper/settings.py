BOT_NAME = "oecd_scraper"

SPIDER_MODULES = ["oecd_scraper.spiders"]
NEWSPIDER_MODULE = "oecd_scraper.spiders"

USER_AGENT = (
    "oecd-ai-research-scraper/0.1 "
    "(+contact: set via OECD_SCRAPER_CONTACT env var)"
)

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
HTTPCACHE_IGNORE_HTTP_CODES = [500, 502, 503, 504, 408, 429]

DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True,
    "timeout": 30_000,
}
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30_000
PLAYWRIGHT_MAX_CONTEXTS = 2
PLAYWRIGHT_MAX_PAGES_PER_CONTEXT = 4

ITEM_PIPELINES = {
    "oecd_scraper.pipelines.ContentImageFilterPipeline": 100,
    "oecd_scraper.pipelines.OecdFilesPipeline": 200,
    "oecd_scraper.pipelines.OecdImagesPipeline": 210,
    "oecd_scraper.pipelines.MarkdownWriterPipeline": 900,
}

FILES_STORE = "output/downloads"
IMAGES_STORE = "output/images"

IMAGES_MIN_HEIGHT = 200
IMAGES_MIN_WIDTH = 200

MEDIA_ALLOW_REDIRECTS = True
FILES_EXPIRES = 90
IMAGES_EXPIRES = 90

FEEDS = {
    "output/pages.jsonl": {
        "format": "jsonlines",
        "encoding": "utf-8",
        "overwrite": False,
        "item_export_kwargs": {
            "ensure_ascii": False,
        },
    },
}

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
FEED_EXPORT_ENCODING = "utf-8"

LOG_LEVEL = "INFO"

DOWNLOAD_MAXSIZE = 100 * 1024 * 1024
DOWNLOAD_WARNSIZE = 32 * 1024 * 1024

DEPTH_PRIORITY = 1
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoDiskQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"
