from __future__ import annotations

import argparse
from pathlib import Path

from release_workflow import (
    finalize_release,
    stage_release,
    validate_current_release,
)

PROJECT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Stage, review, and publish annual 3DV metadata releases."
    )
    commands = root.add_subparsers(dest="command", required=True)
    stage = commands.add_parser(
        "stage", help="Build or resume a candidate without changing published data."
    )
    stage.add_argument(
        "--refresh-citations",
        action="store_true",
        help="Refresh existing OpenAlex and Semantic Scholar rows resumably.",
    )
    stage.add_argument("--skip-openalex", action="store_true")
    stage.add_argument("--skip-semantic-scholar", action="store_true")
    stage.add_argument(
        "--restart",
        action="store_true",
        help="Discard the existing .work candidate and start a fresh stage.",
    )
    commands.add_parser(
        "finalize", help="Validate all manual gates and publish the candidate."
    )
    commands.add_parser(
        "validate", help="Validate the currently published release without APIs."
    )
    return root


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "stage":
        stage_release(
            PROJECT,
            refresh_citations=args.refresh_citations,
            skip_openalex=args.skip_openalex,
            skip_semantic_scholar=args.skip_semantic_scholar,
            restart=args.restart,
        )
    elif args.command == "finalize":
        finalize_release(PROJECT)
    else:
        validate_current_release(PROJECT)


if __name__ == "__main__":
    main()
