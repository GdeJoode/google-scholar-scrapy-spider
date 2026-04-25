"""Summarise every PDF / PPTX in a spider's downloads folder.

Pipeline per document:

  1. Parse with docling (handles PDF and PPTX, falls back to OCR on
     scanned PDFs).
  2. Extract metadata + a hierarchical topic tree by prompting an LLM
     on the first ~8k characters of the parsed text.
  3. RAPTOR-style hierarchical summarisation:
        * chunk the text
        * embed chunks locally with sentence-transformers
        * cluster embeddings with a Gaussian mixture
        * recursively summarise clusters until one root summary remains
  4. Write `output/<spider>/summaries/<original-relpath>.md` with YAML
     frontmatter (title / authors / publication_year / publication_month
     / organisation / topics) and the RAPTOR tree + topic tree as body.

LLM:
    Uses NVIDIA Build (build.nvidia.com) by default. Their endpoint is
    OpenAI-compatible, so the standard `openai` Python client works
    with a different base_url. Sign up at https://build.nvidia.com to
    get a free API key (free tier credits, OpenAI-compatible API).

    Set NVIDIA_API_KEY in your environment before running. To use a
    different OpenAI-compatible provider, override --llm-base-url and
    set the same env var with that provider's key.

Usage:
    export NVIDIA_API_KEY=nvapi-...
    python -m site_scraper.summarize_documents elkeregiotelt
    python -m site_scraper.summarize_documents elkeregiotelt --limit 5
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml


DEFAULT_LLM_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_LLM_MODEL = "mistralai/mistral-medium-3-instruct"
DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

CHUNK_WORDS = 600
MAX_TREE_DEPTH = 3
METADATA_HEAD_CHARS = 4000  # smaller prompts -> fewer 504s on free tiers
LLM_RETRIES = 5


def _load_heavy_deps():
    """Import the slow / optional dependencies on demand so that
    --help is fast and missing extras give a clear error message."""
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
    except ImportError as exc:
        sys.exit(
            "docling is required: pip install docling\n"
            f"(import error: {exc})"
        )
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except ImportError as exc:
        sys.exit(
            "sentence-transformers is required: pip install sentence-transformers\n"
            f"(import error: {exc})"
        )
    try:
        from sklearn.mixture import GaussianMixture  # noqa: F401
    except ImportError as exc:
        sys.exit(
            "scikit-learn is required: pip install scikit-learn\n"
            f"(import error: {exc})"
        )
    try:
        from openai import OpenAI  # noqa: F401
    except ImportError as exc:
        sys.exit(
            "openai is required: pip install openai\n"
            f"(import error: {exc})"
        )

    from docling.document_converter import DocumentConverter
    from sentence_transformers import SentenceTransformer
    from sklearn.mixture import GaussianMixture
    from openai import OpenAI
    import numpy as np

    return DocumentConverter, SentenceTransformer, GaussianMixture, OpenAI, np


def _make_client(OpenAI, base_url, env_var):
    api_key = os.environ.get(env_var)
    if not api_key:
        sys.exit(
            f"Set {env_var} in your environment.\n"
            f"NVIDIA Build keys come from https://build.nvidia.com (free tier)."
        )
    return OpenAI(base_url=base_url, api_key=api_key)


def _llm_call(client, model, prompt, *, system=None, max_tokens=800,
              temperature=0.2, retries=LLM_RETRIES):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    last_err = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:  # network / rate limit / 5xx / etc.
            last_err = exc
            # Generous backoff: free-tier gateways often need real seconds
            # to recover from a 504. Caps at 60s.
            wait = min(60, 5 * (2 ** attempt))
            print(
                f"    LLM call failed ({exc!r}); "
                f"retrying in {wait}s (attempt {attempt + 1}/{retries})...",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries} retries: {last_err}")


def _chunk_words(text, words_per_chunk=CHUNK_WORDS):
    """Split text into roughly-equal chunks on word boundaries.
    Cleans up whitespace so embeddings are sensible."""
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    return [
        " ".join(words[i:i + words_per_chunk])
        for i in range(0, len(words), words_per_chunk)
    ]


def _summarise_chunk(client, model, chunk):
    prompt = (
        "Summarise this passage in 3-5 concise sentences. Preserve "
        "concrete facts, names, dates, and any key claims. Do not add "
        "speculation.\n\nPassage:\n" + chunk
    )
    return _llm_call(client, model, prompt, max_tokens=300)


def _summarise_cluster(client, model, summaries):
    joined = "\n\n".join(f"- {s}" for s in summaries)
    prompt = (
        "These are summaries of related passages from one document. "
        "Synthesise them into a single coherent summary of 4-6 "
        "sentences. Keep concrete facts; drop redundancies.\n\n"
        + joined
    )
    return _llm_call(client, model, prompt, max_tokens=400)


def _cluster_labels(np, GaussianMixture, embeddings):
    n = len(embeddings)
    if n <= 2:
        return [0] * n
    n_components = max(2, min(int(n ** 0.5), n // 2))
    gmm = GaussianMixture(
        n_components=n_components, random_state=42, reg_covar=1e-4
    )
    gmm.fit(embeddings)
    return gmm.predict(embeddings).tolist()


def _build_raptor_tree(client, model, embedder, np, GaussianMixture, chunks):
    """Returns a list of levels, each a list of (summary_text, child_indices).
    Level 0 is per-chunk summaries, the last level is a single root."""
    levels = []
    leaf_summaries = []
    for i, chunk in enumerate(chunks):
        leaf_summaries.append(_summarise_chunk(client, model, chunk))
    levels.append([(s, []) for s in leaf_summaries])

    current = leaf_summaries
    for _ in range(MAX_TREE_DEPTH):
        if len(current) <= 2:
            break
        embeds = embedder.encode(current, show_progress_bar=False)
        labels = _cluster_labels(np, GaussianMixture, embeds)
        clusters = defaultdict(list)
        for idx, lbl in enumerate(labels):
            clusters[lbl].append(idx)
        if len(clusters) <= 1:
            break
        next_level = []
        for lbl, idxs in sorted(clusters.items()):
            cluster_texts = [current[i] for i in idxs]
            summary = _summarise_cluster(client, model, cluster_texts)
            next_level.append((summary, idxs))
        levels.append(next_level)
        current = [s for s, _ in next_level]

    if len(current) > 1:
        root = _summarise_cluster(client, model, current)
        levels.append([(root, list(range(len(current))))])
    return levels


METADATA_PROMPT_TEMPLATE = """Extract metadata and a topic tree from this document.

Source URL (may help with publisher / org): {source_url}
Local filename: {filename}

Document text (truncated):
---
{head}
---

Respond with VALID YAML and nothing else (no markdown fences, no
commentary). Use null when a field is unknown. The topics list should
follow a TreeKG-style two-level structure: each entry has a `main`
topic and a list of `subtopics`. Limit to 3-7 main topics.

title: ...
authors:
  - "Last, First"
  - ...
publication_year: 2024
publication_month: 3
organisation: "..."
topics:
  - main: "..."
    subtopics:
      - "..."
      - "..."
  - main: "..."
    subtopics: []
"""


def _extract_metadata(client, model, text, source_url, filename):
    head = text[:METADATA_HEAD_CHARS]
    prompt = METADATA_PROMPT_TEMPLATE.format(
        head=head, source_url=source_url or "(none)", filename=filename
    )
    raw = _llm_call(
        client, model, prompt,
        system=(
            "You are a precise metadata extractor. Output strict YAML "
            "only — no markdown fences, no preamble. Match the schema "
            "exactly."
        ),
        max_tokens=800,
        temperature=0.0,
    )
    # Strip accidental markdown fences if the model added them.
    raw = re.sub(r"^```(?:ya?ml)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```\s*$", "", raw)
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        print(f"    metadata YAML parse failed: {exc}", file=sys.stderr)
        return {"_raw_metadata": raw}
    return data


def _render_tree(levels):
    """Render the RAPTOR tree as readable markdown, root first."""
    if not levels:
        return ""
    out = []
    if levels[-1] and len(levels) > 1:
        out.append("## Overall summary\n")
        out.append(levels[-1][0][0])
        out.append("")
    # Levels in between (skip the root we already emitted, skip raw chunks
    # at level 0 — too verbose).
    if len(levels) >= 3:
        for depth in range(len(levels) - 2, 0, -1):
            out.append(f"## Level {depth} cluster summaries")
            out.append("")
            for i, (txt, _) in enumerate(levels[depth], 1):
                out.append(f"### Cluster {i}")
                out.append("")
                out.append(txt)
                out.append("")
    elif len(levels) == 2:
        # Only chunk summaries + root: include root as "Overall summary"
        # and the chunk summaries as a flat list.
        out.append("## Chunk summaries")
        out.append("")
        for i, (txt, _) in enumerate(levels[0], 1):
            out.append(f"### Chunk {i}")
            out.append("")
            out.append(txt)
            out.append("")
    elif len(levels) == 1:
        # Only one chunk; its summary IS the document summary.
        out.append("## Summary\n")
        out.append(levels[0][0][0])
        out.append("")
    return "\n".join(out)


def _render_topics(topics):
    if not topics:
        return ""
    lines = ["## Topic tree", ""]
    for t in topics:
        if isinstance(t, dict):
            main = t.get("main") or t.get("topic") or ""
            lines.append(f"- **{main}**")
            for st in t.get("subtopics") or []:
                lines.append(f"  - {st}")
        else:
            lines.append(f"- {t}")
    return "\n".join(lines)


def _find_source_url(manifest_path, relpath):
    if not manifest_path.exists():
        return ""
    target = relpath.replace("\\", "/")
    with manifest_path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            local = (entry.get("local_path") or "").replace("\\", "/")
            if local == target or local.endswith("/" + target):
                return entry.get("pdf_url") or ""
    return ""


def _parse_document(DocumentConverter, path):
    converter = DocumentConverter()
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def _summarise_document(path, downloads_dir, summaries_dir, manifest,
                        DocumentConverter, embedder, client, model):
    relpath = str(path.relative_to(downloads_dir))
    out_path = summaries_dir / (relpath + ".md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"  parse: {relpath}")
    try:
        text = _parse_document(DocumentConverter, path)
    except Exception as exc:
        print(f"    parse failed: {exc}", file=sys.stderr)
        return False

    if not text or not text.strip():
        print("    parsed text is empty; skipping")
        return False

    source_url = _find_source_url(manifest, relpath)

    print(f"  metadata + topics ...")
    try:
        metadata = _extract_metadata(client, model, text, source_url, path.name)
    except Exception as exc:
        # A 504 / overload on the metadata call shouldn't kill the doc:
        # we still want the RAPTOR summary, just with empty frontmatter
        # fields. The user can re-run with --force later to retry only
        # the metadata, or fill it in by hand.
        print(
            f"    metadata extraction failed ({exc}); "
            f"continuing with empty metadata.",
            file=sys.stderr,
        )
        metadata = {"_metadata_error": str(exc)}

    chunks = _chunk_words(text)
    if not chunks:
        print("    no chunks; skipping")
        return False
    print(f"  RAPTOR over {len(chunks)} chunk(s) ...")
    try:
        # Lazy import here to avoid pushing numpy import to startup.
        import numpy as _np
        from sklearn.mixture import GaussianMixture as _GMM
        levels = _build_raptor_tree(
            client, model, embedder, _np, _GMM, chunks
        )
    except Exception as exc:
        print(f"    summarisation failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return False

    front = {
        "title": metadata.get("title"),
        "authors": metadata.get("authors") or [],
        "publication_year": metadata.get("publication_year"),
        "publication_month": metadata.get("publication_month"),
        "organisation": metadata.get("organisation"),
        "topics": metadata.get("topics") or [],
        "source_pdf": source_url or None,
        "local_pdf": f"downloads/{relpath}",
        "summarized_at": datetime.now(timezone.utc).isoformat(),
        "llm_model": model,
    }
    if "_raw_metadata" in metadata:
        front["_raw_metadata"] = metadata["_raw_metadata"]
    if "_metadata_error" in metadata:
        front["_metadata_error"] = metadata["_metadata_error"]

    front_yaml = yaml.safe_dump(
        front, sort_keys=False, allow_unicode=True, width=10_000
    )

    body = []
    body.append("---")
    body.append(front_yaml.rstrip())
    body.append("---")
    body.append("")
    body.append(f"# {front['title'] or path.name}")
    body.append("")
    if source_url:
        body.append(f"Source: <{source_url}>")
    body.append(f"Local PDF: `downloads/{relpath}`")
    body.append("")
    body.append(_render_tree(levels))
    body.append("")
    body.append(_render_topics(metadata.get("topics") or []))
    body.append("")

    out_path.write_text("\n".join(body), encoding="utf-8")
    print(f"  wrote {out_path}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("spider", help="Spider name (directory under output/)")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument(
        "--llm-base-url", default=DEFAULT_LLM_BASE_URL,
        help=f"OpenAI-compatible LLM endpoint (default: {DEFAULT_LLM_BASE_URL})",
    )
    parser.add_argument(
        "--llm-api-key-env", default="NVIDIA_API_KEY",
        help="Env var holding the LLM API key (default: NVIDIA_API_KEY)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_LLM_MODEL,
        help=f"LLM model id (default: {DEFAULT_LLM_MODEL})",
    )
    parser.add_argument(
        "--embed-model", default=DEFAULT_EMBED_MODEL,
        help=f"sentence-transformers model id (default: {DEFAULT_EMBED_MODEL})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N documents (useful for testing).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-summarise documents even if a .md output already exists.",
    )
    args = parser.parse_args(argv)

    spider_dir = Path(args.output_dir) / args.spider
    downloads_dir = spider_dir / "downloads"
    summaries_dir = spider_dir / "summaries"
    manifest = spider_dir / "downloads_manifest.jsonl"

    if not downloads_dir.exists():
        print(f"No downloads directory: {downloads_dir}", file=sys.stderr)
        return 1
    summaries_dir.mkdir(parents=True, exist_ok=True)

    DocumentConverter, SentenceTransformer, _GMM, OpenAI, _np = _load_heavy_deps()
    print(f"Loading embedding model: {args.embed_model} ...")
    embedder = SentenceTransformer(args.embed_model)
    client = _make_client(OpenAI, args.llm_base_url, args.llm_api_key_env)

    docs = sorted(
        p for p in downloads_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".pdf", ".pptx"}
    )
    if not docs:
        print(f"No PDF/PPTX files found in {downloads_dir}")
        return 0
    if args.limit:
        docs = docs[: args.limit]
    print(f"Found {len(docs)} document(s) to summarise.")

    successes = failures = skipped = 0
    for path in docs:
        relpath = str(path.relative_to(downloads_dir))
        out_path = summaries_dir / (relpath + ".md")
        if out_path.exists() and not args.force:
            print(f"  skip (already done): {relpath}")
            skipped += 1
            continue
        try:
            ok = _summarise_document(
                path, downloads_dir, summaries_dir, manifest,
                DocumentConverter, embedder, client, args.model,
            )
        except Exception as exc:
            print(f"    unexpected error on {relpath}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            ok = False
        if ok:
            successes += 1
        else:
            failures += 1

    print()
    print(f"Done. {successes} summarised, {failures} failed, {skipped} skipped.")
    print("Build the overview table with:")
    print(f"  python -m site_scraper.build_overview {args.spider}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
