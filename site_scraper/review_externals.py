"""Review external links collected by a previous crawl.

Reads `output/<spider>/pages.jsonl` (and `publications.jsonl` if
present), aggregates every non-root URL by its registrable domain, and
writes two files into the spider's output directory:

  - external_domains.jsonl  : one record per domain with count + samples
  - approved_off_site.txt   : a human-editable list, each line prefixed
                              with '# ' (disabled). Remove the '# ' for
                              domains you want to crawl next.

Then re-run the spider with

    scrapy crawl <spider> -a off_site_from_file=output/<spider>/approved_off_site.txt

to crawl the approved external domains in a second pass.

Usage:
    python -m site_scraper.review_externals <spider-name>
        [--output-dir output] [--top N] [--min-count N] [--samples N]

Example:
    python -m site_scraper.review_externals elkeregiotelt --top 40
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse


def _registrable(host):
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def aggregate_externals(pages_file, pubs_file, root_hosts, samples_per_domain):
    counts = Counter()
    samples = defaultdict(list)
    seen_urls = set()

    def _add(url):
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        host = _registrable(urlparse(url).netloc)
        if not host:
            return
        if any(host == r or host.endswith("." + r) for r in root_hosts):
            return
        counts[host] += 1
        if len(samples[host]) < samples_per_domain:
            samples[host].append(url)

    if pages_file.exists():
        with pages_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for url in item.get("external_links") or []:
                    _add(url)

    # Publications found via search may point at third-party PDFs;
    # treat those origin domains as external candidates too.
    if pubs_file.exists():
        with pubs_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("discovery_method") == "search":
                    _add(item.get("pdf_url"))

    return counts, samples


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("spider", help="spider name (directory under output/)")
    p.add_argument("--output-dir", default="output",
                   help="parent output directory (default: output)")
    p.add_argument("--top", type=int, default=100,
                   help="show / write at most this many domains (default: 100)")
    p.add_argument("--min-count", type=int, default=1,
                   help="only include domains seen at least this many times")
    p.add_argument("--samples", type=int, default=3,
                   help="sample URLs per domain to help you decide (default: 3)")
    p.add_argument("--root-domains", default=None,
                   help="comma-separated root domains to exclude; if omitted, "
                        "inferred from the spider's own crawled URLs")
    args = p.parse_args(argv)

    spider_dir = Path(args.output_dir) / args.spider
    pages_file = spider_dir / "pages.jsonl"
    pubs_file = spider_dir / "publications.jsonl"

    if not pages_file.exists():
        print(f"Not found: {pages_file}", file=sys.stderr)
        print("Has the spider run yet? Expected output structure:", file=sys.stderr)
        print(f"  {spider_dir}/pages.jsonl", file=sys.stderr)
        return 1

    if args.root_domains:
        root_hosts = {_registrable(d.strip()) for d in args.root_domains.split(",") if d.strip()}
    else:
        root_hosts = set()
        with pages_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("off_site"):
                    continue
                host = _registrable(urlparse(item.get("url") or "").netloc)
                if host:
                    root_hosts.add(host)

    counts, samples = aggregate_externals(
        pages_file, pubs_file, root_hosts, args.samples
    )

    ranked = [
        (d, c) for d, c in counts.most_common() if c >= args.min_count
    ][: args.top]

    if not ranked:
        print("No external domains found (or min-count filter removed them all).")
        return 0

    print(f"\nExternal domains referenced from {args.spider} root crawl:\n")
    print(f"{'count':>6}  domain")
    print(f"{'-----':>6}  ------")
    for d, c in ranked:
        print(f"{c:>6}  {d}")
        for url in samples[d][:3]:
            print(f"        e.g. {url}")
    print()

    # Machine-readable
    jsonl_out = spider_dir / "external_domains.jsonl"
    with jsonl_out.open("w", encoding="utf-8") as fh:
        for d, c in ranked:
            fh.write(json.dumps(
                {"domain": d, "count": c, "samples": samples[d]},
                ensure_ascii=False,
            ) + "\n")
    print(f"Wrote {jsonl_out}")

    # Human-editable approved list. All lines commented by default.
    txt_out = spider_dir / "approved_off_site.txt"
    header = (
        "# Approved off-site domains for the next crawl.\n"
        "# Remove the leading '# ' from each line you want to include,\n"
        "# then rerun the spider with:\n"
        f"#   scrapy crawl {args.spider} -a off_site_from_file={txt_out}\n"
        "#\n"
        "# Counts and sample URLs are for context only; the spider only\n"
        "# reads the domain names in un-commented lines.\n\n"
    )
    with txt_out.open("w", encoding="utf-8") as fh:
        fh.write(header)
        for d, c in ranked:
            fh.write(f"# {d}    # count={c}\n")
    print(f"Wrote {txt_out} (edit this, then rerun the spider)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
