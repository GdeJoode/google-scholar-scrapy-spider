from urllib.parse import urlparse

from itemadapter import ItemAdapter

from site_scraper.base import BaseSiteSpider, _safe_slug


class OecdSpider(BaseSiteSpider):
    name = "oecd"
    start_urls = ["https://oecd.ai/en/"]
    root_domains = {"oecd.ai"}
    allowed_path_prefix = "/en/"
    deny_path_prefixes = ("/en/data",)
    allow_off_site = False

    def file_group_path(self, item):
        """Group downloads by the OECD section, e.g.
        oecd.ai/policy-initiatives/, oecd.ai/dashboards/, oecd.ai/wonk/.
        Skips the /en/ language prefix.
        """
        adapter = ItemAdapter(item)
        url = adapter.get("origin_page_url") or adapter.get("url") or ""
        if not url:
            return "oecd.ai"
        parts = [p for p in urlparse(url).path.split("/") if p]
        if parts and parts[0].lower() == "en" and len(parts) > 1:
            return f"oecd.ai/{_safe_slug(parts[1])}"
        return "oecd.ai"
