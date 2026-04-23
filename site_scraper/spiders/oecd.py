from site_scraper.base import BaseSiteSpider


class OecdSpider(BaseSiteSpider):
    name = "oecd"
    start_urls = ["https://oecd.ai/en/"]
    root_domains = {"oecd.ai"}
    allowed_path_prefix = "/en/"
    deny_path_prefixes = ("/en/data",)
    allow_off_site = False
