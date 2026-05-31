"""Run SC26 packet review artifacts from saved/imported graphs.

Example:
    uv run python scripts/sc26_packet_review.py --paper pap111s2 --force
"""

import argparse
import asyncio
import json

from apps.paper_review.manifest import manifest as paper_review_manifest
from protoneo.api.app import create_app
from protoneo.config.schema import ProtoNeoConfig


def _parse_model_map(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    return json.loads(raw)


async def _run(args: argparse.Namespace) -> None:
    # Initializes the shared API/session/LLM singletons used by the app runner.
    create_app(ProtoNeoConfig.from_env(), apps=[paper_review_manifest])

    from apps.paper_review.api import run_sc26_packet_reviews

    result = await run_sc26_packet_reviews(
        packet_root=args.packet_root,
        paper_ids=args.paper or None,
        conference=args.conference,
        model_map=_parse_model_map(args.model_map_json),
        preset=args.preset,
        max_rounds=args.max_rounds,
        force=args.force,
        skip_completed=not args.no_skip_completed,
        artifact_description_assumed_present=not args.no_ad_assumed_present,
        user_instructions=args.user_instructions,
    )
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SC26 packet review artifacts")
    parser.add_argument("--packet-root", default="submission_packets_sc26")
    parser.add_argument("--paper", action="append", help="Paper id such as pap111s2; repeatable")
    parser.add_argument("--conference", default="sc26")
    parser.add_argument("--model-map-json", default="{}")
    parser.add_argument("--preset", default="")
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-skip-completed", action="store_true")
    parser.add_argument("--no-ad-assumed-present", action="store_true")
    parser.add_argument("--user-instructions", default="")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
