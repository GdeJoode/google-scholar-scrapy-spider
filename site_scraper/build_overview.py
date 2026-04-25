"""Build an overview table from per-document summary markdowns.

Reads the YAML frontmatter from every `output/<spider>/summaries/**/*.md`
(except files starting with `_`) and writes a single
`output/<spider>/summaries/_overview.md` with a markdown table:

    | Title | Date | Organisation | Topics | Summary | Source PDF |

Usage:
    python -m site_scraper.build_overview <spider>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_frontmatter(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _format_date(year, month) -> str:
    if not year:
        return ""
    try:
        if month:
            return f"{int(year)}-{int(month):02d}"
        return str(int(year))
    except (TypeError, ValueError):
        return f"{year}"


def _format_topics(topics) -> str:
    if not topics:
        return ""
    flat = []
    for t in topics:
        if isinstance(t, dict):
            main = (t.get("main") or t.get("topic") or "").strip()
            if main:
                flat.append(main)
        elif t:
            flat.append(str(t).strip())
    return ", ".join(flat)


def _escape_pipe(s) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spider")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args(argv)

    summaries_dir = Path(args.output_dir) / args.spider / "summaries"
    if not summaries_dir.exists():
        print(f"No summaries directory: {summaries_dir}", file=sys.stderr)
        return 1

    rows = []
    for md_path in sorted(summaries_dir.rglob("*.md")):
        if md_path.name.startswith("_"):
            continue
        meta = _read_frontmatter(md_path)
        rows.append({
            "title": meta.get("title") or md_path.stem,
            "date": _format_date(
                meta.get("publication_year"), meta.get("publication_month")
            ),
            "organisation": meta.get("organisation") or "",
            "authors": ", ".join(
                a for a in (meta.get("authors") or []) if a
            ),
            "topics": _format_topics(meta.get("topics") or []),
            "summary_relpath": md_path.relative_to(summaries_dir).as_posix(),
            "source_pdf": meta.get("source_pdf") or "",
            "local_pdf": meta.get("local_pdf") or "",
        })

    # Sort: most recent first by year/month, then alphabetically by title.
    def _sort_key(r):
        date = r["date"] or "0000"
        return (-int(date.split("-")[0]) if date[:4].isdigit() else 0,
                -int(date.split("-")[1]) if "-" in date and date.split("-")[1].isdigit() else 0,
                (r["title"] or "").lower())

    try:
        rows.sort(key=_sort_key)
    except Exception:
        rows.sort(key=lambda r: (r["title"] or "").lower())

    out_path = summaries_dir / "_overview.md"
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# Document overview — {args.spider}\n\n")
        f.write(f"_{len(rows)} document(s) summarised._\n\n")
        f.write("| Title | Date | Organisation | Topics | Summary | Source PDF |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in rows:
            summary_link = f"[md](./{r['summary_relpath']})"
            source_link = (
                f"[pdf]({r['source_pdf']})" if r["source_pdf"] else ""
            )
            f.write(
                f"| {_escape_pipe(r['title'])}"
                f" | {r['date']}"
                f" | {_escape_pipe(r['organisation'])}"
                f" | {_escape_pipe(r['topics'])}"
                f" | {summary_link}"
                f" | {source_link} |\n"
            )

    print(f"Wrote {out_path} with {len(rows)} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
