import hashlib
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from itemadapter import ItemAdapter
from scrapy.pipelines.files import FilesPipeline
from scrapy.pipelines.images import ImagesPipeline


ICON_URL_PATTERN = re.compile(
    r"(favicon|sprite|/icons?/|[/-]icon[-_.]|/logos?/|logo\.|flag[s]?/)",
    re.IGNORECASE,
)


class ContentImageFilterPipeline:
    """Drop URLs that clearly point at chrome (favicons, logos, flag sprites).

    The size-based filter is applied later by ImagesPipeline via
    IMAGES_MIN_WIDTH / IMAGES_MIN_HEIGHT.
    """

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        urls = adapter.get("image_urls") or []
        kept = [u for u in urls if not ICON_URL_PATTERN.search(u)]
        adapter["image_urls"] = kept
        return item


def _safe_filename(stem, max_len=80):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return cleaned[:max_len] or "file"


class SiteFilesPipeline(FilesPipeline):
    """Preserve the original filename for downloads (PDF/DOC/XLS/...)."""

    def file_path(self, request, response=None, info=None, *, item=None):
        parsed = urlparse(request.url)
        basename = os.path.basename(parsed.path) or "file"
        stem, ext = os.path.splitext(basename)
        digest = hashlib.sha1(request.url.encode("utf-8")).hexdigest()[:8]
        return f"{_safe_filename(stem)}_{digest}{ext.lower()}"


class SiteImagesPipeline(ImagesPipeline):
    """Use default hash-based filenames for images; honours min-size from settings."""


class MarkdownWriterPipeline:
    """Write a single pages.markdown file with per-page sections and
    rewritten links that point at the locally downloaded images and files."""

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
        adapter = ItemAdapter(item)
        markdown = adapter.get("markdown") or ""

        url_to_local = {}
        for info in adapter.get("images") or []:
            path = info.get("path")
            url = info.get("url")
            if path and url:
                url_to_local[url] = f"./{self.images_subdir}/{path}"
        for info in adapter.get("files") or []:
            path = info.get("path")
            url = info.get("url")
            if path and url:
                url_to_local[url] = f"./{self.files_subdir}/{path}"

        for remote, local in url_to_local.items():
            markdown = markdown.replace(remote, local)

        title = (adapter.get("title") or adapter.get("url") or "").strip()
        source_url = adapter.get("url") or ""

        external_links = adapter.get("external_links") or []
        if external_links:
            ext_block = "\n\n## External links (not crawled)\n\n" + "\n".join(
                f"- <{link}>" for link in external_links
            )
        else:
            ext_block = ""

        section = (
            f"\n\n# {title}\n\n"
            f"Source: <{source_url}>\n\n"
            f"{markdown.strip()}"
            f"{ext_block}\n\n"
            f"---\n"
        )

        with self._lock:
            self._fh.write(section)
            self._fh.flush()

        return item
