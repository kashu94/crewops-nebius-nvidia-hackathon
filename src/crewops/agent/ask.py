"""Ask one question from the terminal.

    uv run python -m crewops.agent.ask "Who is on reserve at BLR on 13 Oct?"
    uv run python -m crewops.agent.ask --role crew --crew-id C-1042 "How many duty hours do I have?"
    uv run python -m crewops.agent.ask --live "Who covers C-1042?"
    uv run python -m crewops.agent.ask --live --no-model "Who covers C-1042?"

Without ``--live`` it answers from the seed files, with no AWS calls. With ``--live`` it reads
the operation from DynamoDB (so open disruptions count), uses the intents index for the
router's semantic stage, and calls the Nova models (triage, advisor, explainer) and the
guardrail when ``CREWOPS_GUARDRAIL_ID`` is set; ``--no-model`` leaves the models out. The
AWS profile comes from ``.env``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from crewops.domain import Role


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--role", default="controller", choices=[r.value for r in Role])
    parser.add_argument("--crew-id", help="the caller's own crew id, for the crew role")
    parser.add_argument("--live", action="store_true", help="read the live table and index")
    parser.add_argument("--json", action="store_true", help="print the full answer record")
    parser.add_argument(
        "--no-model", action="store_true", help="with --live: rules and templates only"
    )
    args = parser.parse_args(argv)

    from crewops.agent.pipeline import Pipeline
    from crewops.config import Settings, load_env_file
    from crewops.engine import build_world
    from crewops.ingestion.seed import default_seed_dir, load_seed
    from crewops.tools import Caller, CrewOpsTools

    examples_path = default_seed_dir().parent / "eval" / "questions.json"
    examples = {q["question_id"]: q["prompt"] for q in json.loads(examples_path.read_text())}
    caller = Caller("cli", frozenset({Role(args.role)}), args.crew_id)

    if not args.live:
        seed = load_seed()
        pipeline = Pipeline.offline(CrewOpsTools(build_world(seed), seed), examples)
        return _print(pipeline.answer(args.question, caller), args.json)

    load_env_file()
    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1
    from botocore.exceptions import BotoCoreError, ClientError

    from crewops.agent.service import build
    from crewops.store.ops_store import table_for

    settings = Settings.from_env()
    try:
        live = build(settings, table_for(settings), models=not args.no_model)
        answer, used = live.ask(args.question, caller)
    except (ClientError, BotoCoreError) as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1
    code = _print(answer, args.json)
    if used["calls"]:
        print(
            f"[models: {used['calls']} calls, {used['input_tokens']} tokens in, "
            f"{used['output_tokens']} out]"
        )
    return code


def _print(answer, as_json: bool) -> int:
    record = answer.to_dict()
    if as_json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return 0
    print(record["text"])
    route = record["route"] or {}
    for note in record["notes"]:
        print(f"note: {note}")
    print(
        f"\n[{route.get('intent')} via {route.get('decided_by')}"
        f"{' ' + str(route.get('matched')) if route.get('matched') else ''}; "
        f"answer from: {record['source']}"
        f"{'; ' + record['verification'] if record['verification'] else ''}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
