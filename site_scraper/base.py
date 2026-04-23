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

Publication (PDF) discovery is layered:

  1. Direct: any <a href> ending in a document extension.
  2. Inline scan: regex over the full rendered HTML (including inline
     JS / JSON blobs / data-* attributes), so email-gated download
     buttons whose PDF is publicly hosted still get picked up.
  3. Search fallback: if a page looks like an email-gated download
     for a single publication but no PDF URL was found inline, do
     ONE search query (DuckDuckGo HTML by default) for the title,
     fuzzy-match candidate results, and fetch the best match.

Subclass BaseSiteSpider and set:
  - name
  - start_urls
  - root_domains
  - allowed_path_prefix (optional)
  - deny_path_prefixes (optional)
  - allow_off_site (bool)
"""
import difflib
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote, urldefrag, urljoin, urlparse, parse_qs

import scrapy
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from scrapy.http import HtmlResponse
from scrapy_playwright.page import PageMethod

from site_scraper.items import PageItem, PublicationItem


DOWNLOAD_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".csv", ".zip",
)

_DOC_EXT_PATTERN = r"(?:pdf|docx?|xlsx?|pptx?|csv|zip)"

# Absolute http(s) URLs ending in a document extension — used to catch
# PDF references buried in inline JSON, data-* attributes, script blobs,
# etc. so email-gated "download" buttons still produce a PDF when the
# file itself is publicly hosted.
_ABS_DOC_URL_RE = re.compile(
    r'''https?://[^\s"'<>()\[\]{}\\]+?\.''' + _DOC_EXT_PATTERN
    + r'''(?:[?#][^\s"'<>()\[\]{}\\]*)?''',
    re.IGNORECASE,
)
# Root-relative variants (/path/to/file.pdf) that show up in quoted
# attribute values or JS strings.
_REL_DOC_URL_RE = re.compile(
    r'''["'(\s](/[^\s"'<>()\[\]{}\\]+?\.''' + _DOC_EXT_PATTERN
    + r'''(?:[?#][^\s"'<>()\[\]{}\\]*)?)''',
    re.IGNORECASE,
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

PUBLICATION_KEYWORDS = (
    "download", "pdf", "publicatie", "publication",
    "whitepaper", "white paper", "rapport", "report",
)


def _playwright_meta():
    return {
        "playwright": True,
        "playwright_page_methods": [
            PageMethod("wait_for_load_state", "networkidle", timeout=20_000),
        ],
    }


def _registrable_domain(host):
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _fuzzy_score(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


class BaseSiteSpider(scrapy.Spider):
    root_domains: set = set()
    allowed_path_prefix: str = ""
    deny_path_prefixes: tuple = ()
    allow_off_site: bool = False

    # Publication-search fallback settings
    publication_search_enabled: bool = True
    publication_search_min_ratio: float = 0.55
    publication_search_engine: str = "duckduckgo"  # only engine supported out of the box

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
            "SITE_DOWNLOADS_MANIFEST",
            f"output/{name}/downloads_manifest.jsonl",
            priority="spider",
        )
        settings.set(
            "FEEDS",
            {
                f"output/{name}/pages.jsonl": {
                    "format": "jsonlines",
                    "encoding": "utf-8",
                    "overwrite": True,
                    "item_classes": ["site_scraper.items.PageItem"],
                    "item_export_kwargs": {"ensure_ascii": False},
                },
                f"output/{name}/publications.jsonl": {
                    "format": "jsonlines",
                    "encoding": "utf-8",
                    "overwrite": True,
                    "item_classes": ["site_scraper.items.PublicationItem"],
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
        direct_file_urls = self._collect_direct_file_urls(full_soup, response.url)
        inline_file_urls = self._collect_inline_file_urls(response.text, response.url)

        # The page item's own file_urls: both direct links and inline-
        # discovered references (inline ones will also be mirrored as
        # PublicationItems further down so we track discovery method).
        seen = set(direct_file_urls)
        file_urls = list(direct_file_urls)
        for u in inline_file_urls:
            if u not in seen:
                seen.add(u)
                file_urls.append(u)

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

        # Emit one PublicationItem per URL that was only found by inline
        # scan (not as a plain <a href>). That keeps the provenance
        # visible in publications.jsonl.
        inline_only = [u for u in inline_file_urls if u not in set(direct_file_urls)]
        for url in inline_only:
            yield PublicationItem(
                origin_page_url=response.url,
                publication_title=self._page_title(full_soup, content_node),
                discovery_method="inline-scan",
                search_engine="",
                search_query="",
                fuzzy_score=None,
                pdf_url=url,
                file_urls=[url],
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )

        # Search fallback: page looks like an email-gated publication
        # download but we didn't find any PDF link or inline reference.
        if (
            not file_urls
            and self.publication_search_enabled
            and self._looks_email_gated(full_soup)
        ):
            pub_title = self._page_title(full_soup, content_node)
            if pub_title:
                yield self._build_search_request(pub_title, response.url)

        for link, target_off_site_domain in follow_targets:
            yield self._build_request(link, off_site_domain=target_off_site_domain)

    def _page_title(self, full_soup, content_node):
        if content_node is not None:
            h1 = content_node.find("h1")
            if h1 and h1.get_text(strip=True):
                return h1.get_text(" ", strip=True)
        title = full_soup.find("title")
        if title and title.get_text(strip=True):
            return title.get_text(" ", strip=True)
        return ""

    def _looks_email_gated(self, soup):
        """Heuristic: page has an email input inside a form, and the
        surrounding text mentions a publication/download keyword."""
        email_input = soup.find(
            lambda t: t.name == "input"
            and (t.get("type", "").lower() == "email"
                 or "email" in (t.get("name", "") or "").lower())
        )
        if not email_input:
            return False
        page_text = soup.get_text(" ", strip=True).lower()
        return any(kw in page_text for kw in PUBLICATION_KEYWORDS)

    def _build_search_request(self, title, origin_page_url):
        query = f'"{title}" filetype:pdf'
        search_url = (
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        )
        meta = {
            "playwright": False,
            "publication_title": title,
            "origin_page_url": origin_page_url,
            "search_query": query,
            "search_engine": "duckduckgo",
        }
        return scrapy.Request(
            search_url,
            callback=self.parse_search,
            meta=meta,
            errback=self.errback,
            dont_filter=True,
        )

    def parse_search(self, response):
        title = response.meta["publication_title"]
        origin = response.meta["origin_page_url"]
        query = response.meta["search_query"]
        engine = response.meta["search_engine"]

        candidates = self._extract_ddg_pdf_candidates(response)

        best_url, best_score = None, 0.0
        for url, link_text in candidates:
            basename = urlparse(url).path.rsplit("/", 1)[-1]
            score = max(
                _fuzzy_score(title, link_text or ""),
                _fuzzy_score(title, basename),
            )
            if score > best_score:
                best_url, best_score = url, score

        if best_url and best_score >= self.publication_search_min_ratio:
            self.logger.info(
                "Publication search hit for %r: %s (score=%.2f)",
                title, best_url, best_score,
            )
            yield PublicationItem(
                origin_page_url=origin,
                publication_title=title,
                discovery_method="search",
                search_engine=engine,
                search_query=query,
                fuzzy_score=round(best_score, 3),
                pdf_url=best_url,
                file_urls=[best_url],
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            self.logger.info(
                "No fuzzy match for publication %r (best score=%.2f, candidates=%d)",
                title, best_score, len(candidates),
            )

    def _extract_ddg_pdf_candidates(self, response):
        """Pull (url, link_text) pairs out of a DuckDuckGo HTML results
        page, keeping only PDF-looking URLs."""
        soup = BeautifulSoup(response.text, "lxml")
        out = []
        for a in soup.select("a.result__a, a.result__url, a"):
            href = a.get("href") or ""
            if not href:
                continue
            # DDG wraps real URLs in a redirect: /l/?uddg=<encoded>&...
            url = self._unwrap_ddg_url(href)
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                continue
            path = parsed.path.lower()
            if not (path.endswith(".pdf") or ".pdf" in parsed.query.lower()):
                continue
            out.append((url, a.get_text(" ", strip=True)))
        # dedupe preserving order
        seen = set()
        deduped = []
        for url, text in out:
            if url in seen:
                continue
            seen.add(url)
            deduped.append((url, text))
        return deduped

    def _unwrap_ddg_url(self, href):
        if href.startswith("//"):
            href = "https:" + href
        if "duckduckgo.com/l/" in href or href.startswith("/l/"):
            qs = parse_qs(urlparse(href).query)
            wrapped = qs.get("uddg") or qs.get("u")
            if wrapped:
                return unquote(wrapped[0])
            return None
        if href.startswith(("http://", "https://")):
            return href
        return None

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

    def _collect_direct_file_urls(self, soup, base_url):
        urls, seen = [], set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute, _ = urldefrag(urljoin(base_url, href))
            if urlparse(absolute).scheme not in ("http", "https"):
                continue
            path = urlparse(absolute).path.lower()
            if path.endswith(DOWNLOAD_EXTENSIONS) and absolute not in seen:
                seen.add(absolute)
                urls.append(absolute)
        return urls

    def _collect_inline_file_urls(self, raw_html, base_url):
        urls, seen = [], set()
        for match in _ABS_DOC_URL_RE.findall(raw_html):
            absolute, _ = urldefrag(match)
            if urlparse(absolute).scheme not in ("http", "https"):
                continue
            path = urlparse(absolute).path.lower()
            if path.endswith(DOWNLOAD_EXTENSIONS) and absolute not in seen:
                seen.add(absolute)
                urls.append(absolute)
        for match in _REL_DOC_URL_RE.findall(raw_html):
            absolute, _ = urldefrag(urljoin(base_url, match))
            if urlparse(absolute).scheme not in ("http", "https"):
                continue
            path = urlparse(absolute).path.lower()
            if path.endswith(DOWNLOAD_EXTENSIONS) and absolute not in seen:
                seen.add(absolute)
                urls.append(absolute)
        return urls

    def _classify_links(self, soup, base_url, current_off_site_domain):
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
                if self.allow_off_site:
                    followed.append((absolute, host))
                else:
                    external.append(absolute)
            else:
                if host == current_off_site_domain:
                    followed.append((absolute, current_off_site_domain))
                else:
                    external.append(absolute)

        return followed, external

    def _is_root_host(self, host):
        host = _registrable_domain(host)
        for root in self.root_domains:
            root = _registrable_domain(root)
            if host == root or host.endswith("." + root):
                return True
        return False
