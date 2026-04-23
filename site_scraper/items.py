import scrapy


class PageItem(scrapy.Item):
    url = scrapy.Field()
    title = scrapy.Field()
    description = scrapy.Field()
    text = scrapy.Field()
    markdown = scrapy.Field()
    image_urls = scrapy.Field()
    images = scrapy.Field()
    file_urls = scrapy.Field()
    files = scrapy.Field()
    depth = scrapy.Field()
    off_site = scrapy.Field()
    off_site_domain = scrapy.Field()
    external_links = scrapy.Field()
    fetched_at = scrapy.Field()


class PublicationItem(scrapy.Item):
    """A publication (usually a PDF) discovered for a source page.

    Created for both inline-scanned finds (email-gated download button but
    the file itself is referenced in inline JS / JSON / data attributes)
    and search-engine fallback finds (one query via DuckDuckGo, fuzzy-
    matched against the publication title).
    """

    origin_page_url = scrapy.Field()
    publication_title = scrapy.Field()
    discovery_method = scrapy.Field()  # "inline-scan" | "search"
    search_engine = scrapy.Field()
    search_query = scrapy.Field()
    fuzzy_score = scrapy.Field()
    pdf_url = scrapy.Field()
    file_urls = scrapy.Field()
    files = scrapy.Field()
    fetched_at = scrapy.Field()
