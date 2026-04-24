from site_scraper.base import BaseSiteSpider


class DigitalEconomySpider(BaseSiteSpider):
    """Crawl Stanford Digital Economy Lab's site. Stays strictly within
    `digitaleconomy.stanford.edu` by default; other Stanford subdomains
    and outside links are recorded as `external_links` for review.

    Approve specific external domains for a follow-up run via

        scrapy crawl digitaleconomy \\
            -a off_site_from_file=output/digitaleconomy/approved_off_site.txt
    """

    name = "digitaleconomy"
    start_urls = ["https://digitaleconomy.stanford.edu/"]
    root_domains = {"digitaleconomy.stanford.edu"}
