"""Shared base spider.

Crawl rules:
  - From the root site, crawl freely within `root_domains` (optionally
    constrained by `allowed_path_prefix` / `deny_path_prefixes`).
  - If `allow_off_site` is True, a link from the root site to an
    external domain X is followed, and the crawl is allowed to keep
    going within X. It is NOT allowed to hop onwards to a third domain
    Y; any such link is recorded in `external_links` on that page so
    the user can review later and decide whether to crawl Y.
  - If `allow_off_site` is False, external links from the root site
    are only recorded, never followed.

Subclass BaseSiteSpider and set:
  - name
  - start_urls
  - root_domains
  - allowed_path_prefix (optional)
  - deny_path_prefixes (optional)
  - allow_off_site (bool)
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


def _registrable_domain(host):
    """Strip a leading 'www.' so we can match 'example.nl' against
    'www.example.nl' as the same site."""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


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
            yield self._build_request(url, off_site_domain=None)

    def _build_request(self, url, *, off_site_domain):
        meta = _playwright_meta()
        meta["off_site_domain"] = off_site_domain
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

        off_site_domain = response.meta.get("off_site_domain")

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

        follow_targets, external_links = self._classify_links(
            full_soup, response.url, off_site_domain
        )

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
            off_site=off_site_domain is not None,
            off_site_domain=off_site_domain or "",
            external_links=external_links,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        for link, target_off_site_domain in follow_targets:
            yield self._build_request(link, off_site_domain=target_off_site_domain)

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

    def _classify_links(self, soup, base_url, current_off_site_domain):
        """Return (followed, external).

        followed: list of (absolute_url, new_off_site_domain_for_that_request)
        external: list of absolute URLs that are not followed but recorded.
        """
        followed = []
        external = []
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
                # Handled by FilesPipeline, not followed as a page.
                continue

            host = _registrable_domain(parsed.netloc)

            if self._is_root_host(host):
                if self.allowed_path_prefix and not path.startswith(self.allowed_path_prefix):
                    continue
                if any(path == p or path.startswith(p + "/") for p in self.deny_path_prefixes):
                    continue
                followed.append((absolute, None))
                continue

            if current_off_site_domain is None:
                # We're on the root site; this is a first external link.
                if self.allow_off_site:
                    followed.append((absolute, host))
                else:
                    external.append(absolute)
            else:
                # We're already on an external site.
                if host == current_off_site_domain:
                    # Same external site, keep crawling it.
                    followed.append((absolute, current_off_site_domain))
                else:
                    # Would be a second hop to yet another domain — record only.
                    external.append(absolute)

        return followed, external

    def _is_root_host(self, host):
        host = _registrable_domain(host)
        for root in self.root_domains:
            root = _registrable_domain(root)
            if host == root or host.endswith("." + root):
                return True
        return False
