"""Ingest a corpus into graphrag and verify it is retrievable.

    python -m autosentry_agent.ingestion corpora/regulatory.toml

GRAPHRAG_API_TOKEN is read through the SecretsProvider. GRAPHRAG_BASE_URL
defaults to a local graphrag at http://localhost:8000.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import httpx

from ..config import get_secrets_provider
from .corpus import load_corpus
from .driver import run
from .graphrag_current import CurrentGraphragBackend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m autosentry_agent.ingestion")
    parser.add_argument("corpus", type=Path, help="corpus TOML file")
    parser.add_argument("--cache-dir", type=Path, default=Path(".corpus_cache"))
    parser.add_argument(
        "--base-url", default=os.environ.get("GRAPHRAG_BASE_URL", "http://localhost:8000")
    )
    parser.add_argument("--timeout", type=float, default=7200.0, help="seconds to wait for processing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    corpus = load_corpus(args.corpus)
    token = get_secrets_provider().get_secret("GRAPHRAG_API_TOKEN")

    with httpx.Client(timeout=httpx.Timeout(180.0)) as http:
        backend = CurrentGraphragBackend(args.base_url, token, http=http)
        report = run(corpus, backend, cache_dir=args.cache_dir, http=http, timeout=args.timeout)

    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
