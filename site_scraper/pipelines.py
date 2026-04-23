import hashlib
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from itemadapter import ItemAdapter
from scrapy.pipelines.files import FilesPipeline
from scrapy.pipelines.images import ImagesPipeline

from site_scraper.items import PageItem, PublicationItem


ICON_URL_PATTERN = re.compile(
    r"(favicon|sprite|/icons?/|[/-]icon[-_.]|/logos?/|logo\.|flag[s]?/)",
    re.IGNORECASE,
)

# Matches the WordPress thumbnail suffix in image URLs, e.g.
# "foo-124x124.jpg" or "bar-300x200.png". Lets us drop sub-min-size
# thumbnails before hitting ImagesPipeline so we don't spam the log
# with one ERROR per rejected thumbnail.
THUMB_SIZE_RE = re.compile(
    r"-(\d+)x(\d+)\.(?:jpe?g|png|gif|webp)(?:$|\?)",
    re.IGNORECASE,
)


def _url_hints_too_small(url, min_w, min_h):
    m = THUMB_SIZE_RE.search(url)
    if not m:
        return False
    w, h = int(m.group(1)), int(m.group(2))
    return w < min_w or h < min_h


class ContentImageFilterPipeline:
    """Drop URLs that clearly point at chrome (favicons, logos, flag
    sprites) or that the filename already reveals to be sub-min-size
    WordPress thumbnails."""

    def __init__(self, min_w=200, min_h=200):
        self.min_w = min_w
        self.min_h = min_h

    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        return cls(
            min_w=settings.getint("IMAGES_MIN_WIDTH", 200),
            min_h=settings.getint("IMAGES_MIN_HEIGHT", 200),
        )

    def process_item(self, item, spider):
        if not isinstance(item, PageItem):
            return item
        adapter = ItemAdapter(item)
        urls = adapter.get("image_urls") or []
        kept = []
        for u in urls:
            if ICON_URL_PATTERN.search(u):
                continue
            if _url_hints_too_small(u, self.min_w, self.min_h):
                continue
            kept.append(u)
        adapter["image_urls"] = kept
        return item


def _safe_filename(stem, max_len=80):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return cleaned[:max_len] or "file"


def _subdir_for(info, item):
    """Ask the spider for a grouping subdir for this item. Falls back to
    '' (flat layout) if the spider doesn't expose file_group_path."""
    spider = getattr(info, "spider", None) if info is not None else None
    if spider is None or item is None:
        return ""
    group = getattr(spider, "file_group_path", None)
    if group is None:
        return ""
    try:
        return (group(item) or "").strip("/")
    except Exception:
        return ""


class SiteFilesPipeline(FilesPipeline):
    """Preserve the original filename for downloads (PDF/DOC/XLS/...) and
    group them into per-spider subdirectories via the spider's
    file_group_path(item) hook."""

    def file_path(self, request, response=None, info=None, *, item=None):
        parsed = urlparse(request.url)
        basename = os.path.basename(parsed.path) or "file"
        stem, ext = os.path.splitext(basename)
        digest = hashlib.sha1(request.url.encode("utf-8")).hexdigest()[:8]
        filename = f"{_safe_filename(stem)}_{digest}{ext.lower()}"
        subdir = _subdir_for(info, item)
        return f"{subdir}/{filename}" if subdir else filename


class SiteImagesPipeline(ImagesPipeline):
    """Hash-named images, honours IMAGES_MIN_WIDTH/HEIGHT, and groups into
    per-spider subdirectories like SiteFilesPipeline.

    Only processes PageItem — PublicationItem has no images.
    """

    def process_item(self, item, spider):
        if not isinstance(item, PageItem):
            return item
        return super().process_item(item, spider)

    def file_path(self, request, response=None, info=None, *, item=None):
        image_guid = hashlib.sha1(request.url.encode("utf-8")).hexdigest()
        subdir = _subdir_for(info, item)
        filename = f"{image_guid}.jpg"
        return f"{subdir}/{filename}" if subdir else f"full/{filename}"


class DownloadsManifestPipeline:
    """Records every downloaded file with its provenance (which page it
    was discovered on, how it was found) and writes a single
    downloads_manifest.jsonl at the end of the crawl."""

    def __init__(self, output_path):
        self.output_path = Path(output_path)
        self._entries = {}  # url -> dict
        self._lock = threading.Lock()

    @classmethod
    def from_crawler(cls, crawler):
        output_path = crawler.settings.get(
            "SITE_DOWNLOADS_MANIFEST",
            "output/downloads_manifest.jsonl",
        )
        return cls(output_path)

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        files = adapter.get("files") or []
        if not files:
            return item

        if isinstance(item, PublicationItem):
            referrer = adapter.get("origin_page_url") or ""
            method = adapter.get("discovery_method") or ""
            extra = {
                "publication_title": adapter.get("publication_title") or "",
                "search_engine": adapter.get("search_engine") or "",
                "search_query": adapter.get("search_query") or "",
                "fuzzy_score": adapter.get("fuzzy_score"),
            }
        else:
            referrer = adapter.get("url") or ""
            method = "direct-link"
            extra = {}

        with self._lock:
            for info in files:
                url = info.get("url")
                path = info.get("path")
                if not url or not path:
                    continue
                entry = self._entries.setdefault(
                    url,
                    {
                        "pdf_url": url,
                        "local_path": path,
                        "discovery_methods": [],
                        "referrers": [],
                        "status": info.get("status"),
                        "checksum": info.get("checksum"),
                        **extra,
                    },
                )
                if method and method not in entry["discovery_methods"]:
                    entry["discovery_methods"].append(method)
                if referrer and referrer not in entry["referrers"]:
                    entry["referrers"].append(referrer)
                # Extra fields (search metadata) are only overwritten if
                # not already set to something non-empty.
                for k, v in extra.items():
                    if v and not entry.get(k):
                        entry[k] = v
        return item

    def close_spider(self, spider):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as fh:
            for entry in self._entries.values():
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class MarkdownWriterPipeline:
    """Write a single pages.markdown file with per-page sections and
    rewritten links that point at the locally downloaded images and files.

    PublicationItems from inline-scan / search are also appended as
    their own short sections so that every downloaded PDF has a visible
    trail in the markdown output."""

    def __init__(self, output_path, images_subdir, files_subdir):
        self.output_path = Path(output_path)
        self.images_subdir = images_subdir
        self.files_subdir = files_subdir
        self._lock = threading.Lock()
        self._fh = None

    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        images_store = settings.get("IMAGES_STORE", "output/images")
        files_store = settings.get("FILES_STORE", "output/downloads")
        images_subdir = os.path.basename(os.path.normpath(images_store)) or "images"
        files_subdir = os.path.basename(os.path.normpath(files_store)) or "downloads"
        output_path = settings.get("SITE_MARKDOWN_OUTPUT", "output/pages.markdown")
        return cls(output_path, images_subdir, files_subdir)

    def open_spider(self, spider):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.output_path, "w", encoding="utf-8")

    def close_spider(self, spider):
        if self._fh:
            self._fh.close()
            self._fh = None

    def process_item(self, item, spider):
        if isinstance(item, PublicationItem):
            self._write_publication(item)
        else:
            self._write_page(item)
        return item

    def _write_page(self, item):
        adapter = ItemAdapter(item)
        markdown = adapter.get("markdown") or ""

        url_to_local = {}
        for info in adapter.get("images") or []:
            if info.get("path") and info.get("url"):
                url_to_local[info["url"]] = f"./{self.images_subdir}/{info['path']}"
        downloads = []
        for info in adapter.get("files") or []:
            if info.get("path") and info.get("url"):
                local = f"./{self.files_subdir}/{info['path']}"
                url_to_local[info["url"]] = local
                downloads.append((info["url"], local))

        for remote, local in url_to_local.items():
            markdown = markdown.replace(remote, local)

        title = (adapter.get("title") or adapter.get("url") or "").strip()
        source_url = adapter.get("url") or ""

        blocks = [f"\n\n# {title}\n\n", f"Source: <{source_url}>\n\n", markdown.strip()]

        if downloads:
            blocks.append("\n\n## Downloads\n\n")
            blocks.append(
                "\n".join(f"- [{remote}]({local})" for remote, local in downloads)
            )

        external_links = adapter.get("external_links") or []
        if external_links:
            blocks.append("\n\n## External links (not crawled)\n\n")
            blocks.append("\n".join(f"- <{link}>" for link in external_links))

        blocks.append("\n\n---\n")

        with self._lock:
            self._fh.write("".join(blocks))
            self._fh.flush()

    def _write_publication(self, item):
        adapter = ItemAdapter(item)
        title = adapter.get("publication_title") or adapter.get("pdf_url") or ""
        origin = adapter.get("origin_page_url") or ""
        method = adapter.get("discovery_method") or ""
        engine = adapter.get("search_engine") or ""
        query = adapter.get("search_query") or ""
        score = adapter.get("fuzzy_score")
        pdf_url = adapter.get("pdf_url") or ""

        local_path = ""
        for info in adapter.get("files") or []:
            if info.get("path") and info.get("url") == pdf_url:
                local_path = f"./{self.files_subdir}/{info['path']}"
                break

        lines = [
            f"\n\n## Publication: {title}\n\n",
            f"- Found on: <{origin}>\n",
            f"- Discovery: {method}",
        ]
        if engine:
            lines.append(f" via {engine}")
        lines.append("\n")
        if query:
            lines.append(f"- Search query: `{query}`\n")
        if score is not None:
            lines.append(f"- Fuzzy score: {score}\n")
        lines.append(f"- PDF: <{pdf_url}>\n")
        if local_path:
            lines.append(f"- Local: [{local_path}]({local_path})\n")
        lines.append("\n---\n")

        with self._lock:
            self._fh.write("".join(lines))
            self._fh.flush()
