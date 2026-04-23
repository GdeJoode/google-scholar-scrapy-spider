import re
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin, urlparse

import scrapy
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapy.http import HtmlResponse
from scrapy_playwright.page import PageMethod

from oecd_scraper.items import PageItem


DOWNLOAD_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".zip",
)

DENY_PATH_PREFIXES = ("/en/data",)

ALLOWED_PATH_PREFIX = "/en/"

CONTENT_CONTAINER_SELECTORS = [
    "main",
    "article",
    "[role=main]",
    "#main",
    "#content",
    ".main-content",
    ".entry-content",
    ".post-content",
    ".page-content",
]

STRIP_SELECTORS = [
    "script", "style", "noscript", "template",
    "header", "footer", "nav", "aside",
    "[role=navigation]", "[role=banner]", "[role=contentinfo]",
    ".cookie", ".cookies", "#cookie", "#cookies",
    ".newsletter", ".share", ".social",
    ".breadcrumb", ".breadcrumbs",
    ".skip-link",
]

PLAYWRIGHT_META = {
    "playwright": True,
    "playwright_page_methods": [
        PageMethod("wait_for_load_state", "networkidle", timeout=20_000),
    ],
}


class OecdSpider(scrapy.Spider):
    name = "oecd"
    allowed_domains = ["oecd.ai"]
    start_urls = ["https://oecd.ai/en/"]

    custom_settings = {
        "DEPTH_LIMIT": 0,
    }

    def start_requests(self):
        for url in self.start_urls:
            yield self._build_request(url)

    def _build_request(self, url):
        return scrapy.Request(
            url,
            callback=self.parse,
            meta=dict(PLAYWRIGHT_META),
            errback=self.errback,
            dont_filter=False,
        )

    def parse(self, response):
        if not isinstance(response, HtmlResponse):
            return

        full_soup = BeautifulSoup(response.text, "lxml")

        file_urls = self._collect_file_urls(full_soup, response.url)
        follow_urls = self._collect_follow_urls(full_soup, response.url)

        content_soup = BeautifulSoup(response.text, "lxml")
        for selector in STRIP_SELECTORS:
            for tag in content_soup.select(selector):
                tag.decompose()

        content_node = None
        for selector in CONTENT_CONTAINER_SELECTORS:
            content_node = content_soup.select_one(selector)
            if content_node is not None:
                break
        if content_node is None:
            content_node = content_soup.body or content_soup

        for tag in content_node.find_all(["img", "a", "source"]):
            for attr in ("src", "href", "data-src", "data-lazy-src", "srcset"):
                val = tag.get(attr)
                if not val or val.startswith("data:"):
                    continue
                if attr == "srcset":
                    first = val.split(",")[0].strip().split(" ")[0]
                    tag[attr] = urljoin(response.url, first) if first else val
                else:
                    tag[attr] = urljoin(response.url, val)

        content_html = str(content_node)

        text = content_node.get_text(" ", strip=True)
        markdown = md(content_html, heading_style="ATX", strip=["script", "style"])
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()

        image_urls = self._collect_image_urls(content_node, response.url)

        yield PageItem(
            url=response.url,
            title=(response.css("title::text").get() or "").strip(),
            description=(
                response.xpath("//meta[@name='description']/@content").get()
                or response.xpath("//meta[@property='og:description']/@content").get()
                or ""
            ).strip(),
            text=text,
            markdown=markdown,
            image_urls=image_urls,
            file_urls=file_urls,
            depth=response.meta.get("depth", 0),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        for link in follow_urls:
            yield self._build_request(link)

    def errback(self, failure):
        self.logger.warning("Request failed: %s", failure.request.url)

    def _collect_image_urls(self, content_node, base_url):
        urls = []
        seen = set()
        for img in content_node.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if not src:
                srcset = img.get("srcset") or img.get("data-srcset")
                if srcset:
                    src = srcset.split(",")[-1].strip().split(" ")[0]
            if not src:
                continue
            if src.startswith("data:"):
                continue
            absolute = urljoin(base_url, src)
            if absolute.lower().endswith(".svg"):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            urls.append(absolute)
        return urls

    def _collect_file_urls(self, soup, base_url):
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:"):
                continue
            absolute, _ = urldefrag(urljoin(base_url, href))
            path = urlparse(absolute).path.lower()
            if path.endswith(DOWNLOAD_EXTENSIONS):
                if absolute not in seen:
                    seen.add(absolute)
                    urls.append(absolute)
        return urls

    def _collect_follow_urls(self, soup, base_url):
        urls = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            if href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            absolute, _ = urldefrag(urljoin(base_url, href))
            if not self._should_follow(absolute):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            urls.append(absolute)
        return urls

    def _should_follow(self, url):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.netloc and parsed.netloc not in self.allowed_domains:
            return False
        path = parsed.path or "/"
        if not path.startswith(ALLOWED_PATH_PREFIX):
            return False
        for prefix in DENY_PATH_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return False
        if path.lower().endswith(DOWNLOAD_EXTENSIONS):
            return False
        return True
