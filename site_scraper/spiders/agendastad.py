from site_scraper.base import BaseSiteSpider


class AgendastadSpider(BaseSiteSpider):
    """Crawl agendastad.nl. City-deal pages sometimes link out to their
    own websites; those are captured exactly one hop deep (landing page
    only, no further crawl on the external domain)."""

    name = "agendastad"
    start_urls = ["https://agendastad.nl/"]
    root_domains = {"agendastad.nl"}
    allow_off_site = True
