"""CLI entry point for slack-monitor.

Usage:
    stail tail -f --format json -c "#general" | slack-monitor
    stail tail -f --format json -c "#general" | slack-monitor --window 30
    stail tail -f --format json -c "#general" | slack-monitor --debug
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from slack_monitor import __version__
from slack_monitor.analyzer import AnalyzerEngine
from slack_monitor.buffer import MessageBuffer
from slack_monitor.config import load_config
from slack_monitor.formatter import Formatter
from slack_monitor.llm import LLMClient


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slack-monitor",
        description=(
            "Real-time Slack channel summarizer.\n"
            "Pipe stail output: stail tail -f --format json -c '#channel' | slack-monitor"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to config.toml (default: ./config.toml or ~/.config/slack-monitor/config.toml)",
    )
    p.add_argument(
        "--channel",
        default="",
        metavar="NAME",
        help="Channel name for display (informational only, does not affect data source)",
    )
    p.add_argument(
        "--model",
        default=None,
        metavar="MODEL_ID",
        help="Override LLM model from config (e.g. openai/gpt-oss-20b)",
    )
    p.add_argument(
        "--window",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Override analysis window duration in seconds",
    )
    p.add_argument(
        "--show-raw",
        action="store_true",
        default=None,
        help="Print raw LLM JSON output below each analysis panel",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to stderr",
    )
    return p


async def _async_main(args: argparse.Namespace) -> None:
    config = load_config(args.config)

    # Apply CLI overrides
    overrides: dict = {}
    if args.model is not None:
        overrides["model"] = args.model
    if args.window is not None:
        overrides["window_seconds"] = args.window
    if args.show_raw:
        overrides["show_raw"] = True
    if overrides:
        config = config.model_copy(update=overrides)

    # Connect asyncio stdin reader
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    engine = AnalyzerEngine(
        config=config,
        llm=LLMClient(config),
        buffer=MessageBuffer(config),
        formatter=Formatter(config),
        channel=args.channel,
    )

    await engine.run(reader)


def main() -> None:
    args = build_parser().parse_args()

    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
