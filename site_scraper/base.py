"""Shared base spider for crawling a root site plus (optionally) one hop
onto any linked external sites.

Subclass BaseSiteSpider and set:
  - name              : Scrapy spider name
  - start_urls        : list of entry URLs
  - root_domains      : set of hostnames considered "on site"
  - allowed_path_prefix : optional; on-site URLs must start with this path
  - deny_path_prefixes  : optional tuple of path prefixes to skip on-site
  - allow_off_site    : if True, follow external links exactly one hop deep
"""
import re
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin, urlparse

import scrapy
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapy.http import HtmlResponse
from scrapy_playwright.page import PageMethod

from site_scraper.items import PageItem


DOWNLOAD_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".zip",
)

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


def _playwright_meta():
    return {
        "playwright": True,
        "playwright_page_methods": [
            PageMethod("wait_for_load_state", "networkidle", timeout=20_000),
        ],
    }


class BaseSiteSpider(scrapy.Spider):
    root_domains: set = set()
    allowed_path_prefix: str = ""
    deny_path_prefixes: tuple = ()
    allow_off_site: bool = False

    @classmethod
    def update_settings(cls, settings):
        super().update_settings(settings)
        name = cls.name
        settings.set("IMAGES_STORE", f"output/{name}/images", priority="spider")
        settings.set("FILES_STORE", f"output/{name}/downloads", priority="spider")
        settings.set(
            "SITE_MARKDOWN_OUTPUT",
            f"output/{name}/pages.markdown",
            priority="spider",
        )
        settings.set(
            "FEEDS",
            {
                f"output/{name}/pages.jsonl": {
                    "format": "jsonlines",
                    "encoding": "utf-8",
                    "overwrite": True,
                    "item_export_kwargs": {"ensure_ascii": False},
                },
            },
            priority="spider",
        )

    def start_requests(self):
        for url in self.start_urls:
            yield self._build_request(url, off_site=False)

    def _build_request(self, url, *, off_site):
        meta = _playwright_meta()
        meta["off_site"] = off_site
        return scrapy.Request(
            url,
            callback=self.parse,
            meta=meta,
            errback=self.errback,
            dont_filter=False,
        )

    def errback(self, failure):
        self.logger.warning("Request failed: %s", failure.request.url)

    def parse(self, response):
        if not isinstance(response, HtmlResponse):
            return

        off_site = bool(response.meta.get("off_site"))

        full_soup = BeautifulSoup(response.text, "lxml")
        file_urls = self._collect_file_urls(full_soup, response.url)

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
            off_site=off_site,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        if off_site:
            # One hop only: capture this page, but don't chase its links.
            return

        for link, target_off_site in self._collect_follow_targets(full_soup, response.url):
            yield self._build_request(link, off_site=target_off_site)

    def _collect_image_urls(self, content_node, base_url):
        urls, seen = [], set()
        for img in content_node.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if not src:
                srcset = img.get("srcset") or img.get("data-srcset")
                if srcset:
                    src = srcset.split(",")[-1].strip().split(" ")[0]
            if not src or src.startswith("data:"):
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
        urls, seen = [], set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute, _ = urldefrag(urljoin(base_url, href))
            path = urlparse(absolute).path.lower()
            if path.endswith(DOWNLOAD_EXTENSIONS):
                if absolute not in seen:
                    seen.add(absolute)
                    urls.append(absolute)
        return urls

    def _collect_follow_targets(self, soup, base_url):
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            absolute, _ = urldefrag(urljoin(base_url, href))
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            path = parsed.path or "/"
            if path.lower().endswith(DOWNLOAD_EXTENSIONS):
                continue
            host = parsed.netloc.lower()
            if self._is_root_host(host):
                if self.allowed_path_prefix and not path.startswith(self.allowed_path_prefix):
                    continue
                if any(path == p or path.startswith(p + "/") for p in self.deny_path_prefixes):
                    continue
                yield absolute, False
            elif self.allow_off_site:
                yield absolute, True

    def _is_root_host(self, host):
        for root in self.root_domains:
            if host == root or host.endswith("." + root):
                return True
        return False
