"""CLI entry points for slack-monitor.

slack-monitor (plain mode):
    Reads stail JSONL from stdin and prints Rich panels to stdout.
    stail tail -f --format json -c "#general" | slack-monitor

slack-monitor-tui (TUI mode):
    Spawns stail internally and displays a live three-panel TUI.
    slack-monitor-tui --channel "#general"
    slack-monitor-tui --channel "#general" --stail-args "--config myconfig.json"
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
from slack_monitor.models import AppConfig


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _add_common_args(p: argparse.ArgumentParser) -> None:
    """Add arguments shared by both CLI modes."""
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to config.toml (default: ./config.toml or ~/.config/slack-monitor/config.toml)",
    )
    p.add_argument(
        "--model",
        default=None,
        metavar="MODEL_ID",
        help="Override LLM model (e.g. openai/gpt-oss-20b)",
    )
    p.add_argument(
        "--window",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Override analysis window duration in seconds",
    )
    p.add_argument(
        "--language",
        default=None,
        metavar="LANG",
        help='Analysis output language (e.g. "Japanese", "English"). Default: auto',
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to stderr",
    )


def _build_config(args: argparse.Namespace) -> AppConfig:
    config = load_config(args.config)
    overrides: dict = {}
    if args.model is not None:
        overrides["model"] = args.model
    if args.window is not None:
        overrides["window_seconds"] = args.window
    if getattr(args, "show_raw", False):
        overrides["show_raw"] = True
    if getattr(args, "language", None) is not None:
        overrides["analysis_language"] = args.language
    if overrides:
        config = config.model_copy(update=overrides)
    return config


def _setup_logging(args: argparse.Namespace) -> None:
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# slack-monitor  (plain / no-tui mode)
# ---------------------------------------------------------------------------

def _build_plain_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slack-monitor",
        description=(
            "Real-time Slack channel summarizer (plain mode).\n"
            "Pipe stail output to stdin:\n"
            "  stail tail -f --format json -c \"#general\" | slack-monitor"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_args(p)
    p.add_argument(
        "--channel",
        default="",
        metavar="NAME",
        help="Channel name for display (informational only)",
    )
    p.add_argument(
        "--show-raw",
        action="store_true",
        default=False,
        help="Print raw LLM JSON output alongside analysis panels",
    )
    return p


async def _plain_run(args: argparse.Namespace) -> None:
    config = _build_config(args)

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
    """Entry point for `slack-monitor` (plain/pipe mode)."""
    args = _build_plain_parser().parse_args()
    _setup_logging(args)
    try:
        asyncio.run(_plain_run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)


# ---------------------------------------------------------------------------
# slack-monitor-tui  (TUI mode — spawns stail internally)
# ---------------------------------------------------------------------------

def _build_tui_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slack-monitor-tui",
        description=(
            "Real-time Slack channel summarizer (TUI mode).\n"
            "Spawns stail internally — no pipe needed:\n"
            "  slack-monitor-tui --channel \"#general\"\n"
            "  slack-monitor-tui --channel \"#general\" --stail-args \"--config myconfig.json\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_args(p)
    p.add_argument(
        "--channel",
        required=True,
        metavar="NAME",
        help="Slack channel name passed to stail (e.g. \"#general\")",
    )
    p.add_argument(
        "--stail-args",
        default="",
        metavar="ARGS",
        help=(
            "Extra arguments forwarded verbatim to stail. "
            "Quote the whole string: --stail-args \"--config myconfig.json\""
        ),
    )
    return p


def tui_main() -> None:
    """Entry point for `slack-monitor-tui` (TUI mode)."""
    from slack_monitor.tui import SlackMonitorApp

    args = _build_tui_parser().parse_args()
    _setup_logging(args)

    config = _build_config(args)
    app = SlackMonitorApp(
        config=config,
        llm=LLMClient(config),
        buffer=MessageBuffer(config),
        formatter=Formatter(config),
        channel=args.channel,
        stail_args=args.stail_args,
    )
    try:
        app.run(mouse=False)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
