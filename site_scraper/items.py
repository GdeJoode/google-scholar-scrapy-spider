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
