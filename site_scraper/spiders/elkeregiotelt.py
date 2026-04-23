from site_scraper.base import BaseSiteSpider


class ElkeregioteltSpider(BaseSiteSpider):
    """Crawl elkeregiotelt.nl (Regio Deals programma + NPVR). By default
    stays within elkeregiotelt.nl and only records external links (to
    individual regio-deal sites, government sites, etc.) in
    `external_links`. Run `python -m site_scraper.review_externals
    elkeregiotelt` after the first crawl to pick which external
    domains to include in a follow-up run."""

    name = "elkeregiotelt"
    start_urls = ["https://www.elkeregiotelt.nl/"]
    root_domains = {"elkeregiotelt.nl"}
