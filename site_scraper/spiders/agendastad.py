from site_scraper.base import BaseSiteSpider


class AgendastadSpider(BaseSiteSpider):
    """Crawl agendastad.nl. By default stays within agendastad.nl and
    only records external links (e.g. to each city-deal's own website)
    in `external_links`. Run `python -m site_scraper.review_externals
    agendastad` after the first crawl to pick which external domains
    to include in a follow-up run."""

    name = "agendastad"
    start_urls = ["https://agendastad.nl/"]
    root_domains = {"agendastad.nl"}
