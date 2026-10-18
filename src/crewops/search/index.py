"""Build and try the search indexes.

    uv run python -m crewops.search.index --dry-run           # count documents, no AWS calls
    uv run python -m crewops.search.index                     # embed and store everything
    uv run python -m crewops.search.index --query "how much rest does a pilot need" --index rules

Documents come from data/seed/rules.json and data/eval/{questions,scenarios}.json. Storing is
idempotent: each document's key replaces its previous vector. Settings and the AWS profile
come from ``.env``, as for the other commands.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from crewops.config import Settings, load_env_file
from crewops.ingestion.seed import default_seed_dir
from crewops.search.vectors import INDEXES, load_documents


def default_eval_dir() -> Path:
    return default_seed_dir().parent / "eval"


def clients(settings: Settings):
    """Bedrock runtime and S3 Vectors clients for the active profile and region."""
    import boto3

    session = boto3.Session(region_name=settings.aws_region)
    return session.client("bedrock-runtime"), session.client("s3vectors")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="count documents only")
    parser.add_argument("--query", help="search instead of indexing")
    parser.add_argument("--index", choices=INDEXES, default="rules")
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args(argv)

    documents = load_documents(default_seed_dir(), default_eval_dir())
    if args.dry_run:
        for name, docs in documents.items():
            print(f"{name}: {len(docs)} documents")
        return 0

    load_env_file()
    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1

    from botocore.exceptions import BotoCoreError, ClientError

    from crewops.search.vectors import VectorSearch, titan_embedder

    settings = Settings.from_env()
    try:
        bedrock, s3vectors = clients(settings)
        embed = titan_embedder(bedrock, settings.embedding_model_id, settings.embedding_dimensions)
        search = VectorSearch(s3vectors, settings.vector_bucket, embed)

        if args.query:
            for hit in search.search(args.index, args.query, top_k=args.top):
                label = hit.metadata.get("text", "")[:90]
                print(f"{hit.key:<14} score {hit.score:.3f}  semantic {hit.semantic:.3f}  {label}")
            return 0

        for name, docs in documents.items():
            stored = search.put(name, docs)
            print(f"{name}: {stored} documents stored in {settings.vector_bucket}")
    except (ClientError, BotoCoreError) as exc:
        # Safe to rerun once the cause is fixed (stack not deployed, model access...).
        # Stored documents just get replaced.
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
