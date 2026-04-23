from site_scraper.base import BaseSiteSpider


class ElkeregioteltSpider(BaseSiteSpider):
    """Crawl elkeregiotelt.nl (Regio Deals programma + NPVR). External
    regio-deal sites are captured exactly one hop deep (landing page
    only)."""

    name = "elkeregiotelt"
    start_urls = ["https://www.elkeregiotelt.nl/"]
    root_domains = {"elkeregiotelt.nl"}
    allow_off_site = True
