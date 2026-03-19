"""CLI entry point for slack-monitor.

Usage (TUI mode, default):
    stail tail -f --format json -c "#general" | slack-monitor --channel general

Usage (plain/no-tui mode):
    stail tail -f --format json -c "#general" | slack-monitor --no-tui
    stail tail -f --format json -c "#general" | slack-monitor --no-tui --debug
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from slack_monitor import __version__
from slack_monitor.analyzer import AnalyzerEngine
from slack_monitor.buffer import MessageBuffer
from slack_monitor.config import load_config
from slack_monitor.formatter import Formatter
from slack_monitor.llm import LLMClient
from slack_monitor.models import AppConfig


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
        help="Channel name for display (informational only)",
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
        help='Analysis output language (e.g. "Japanese", "English"). Default: auto (follows messages)',
    )
    p.add_argument(
        "--show-raw",
        action="store_true",
        default=False,
        help="Print raw LLM JSON output (no-tui mode only)",
    )
    p.add_argument(
        "--no-tui",
        action="store_true",
        default=False,
        help="Disable TUI; print Rich panels to stdout (plain mode)",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to stderr",
    )
    return p


def _build_config(args: argparse.Namespace) -> AppConfig:
    config = load_config(args.config)
    overrides: dict = {}
    if args.model is not None:
        overrides["model"] = args.model
    if args.window is not None:
        overrides["window_seconds"] = args.window
    if args.show_raw:
        overrides["show_raw"] = True
    if getattr(args, "language", None) is not None:
        overrides["analysis_language"] = args.language
    if overrides:
        config = config.model_copy(update=overrides)
    return config


async def _async_main(args: argparse.Namespace) -> None:
    """Plain (no-tui) mode: Rich panels to stdout."""
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


def _run_tui(args: argparse.Namespace) -> None:
    """TUI mode: Textual three-panel interface."""
    from slack_monitor.tui import SlackMonitorApp

    config = _build_config(args)

    # Duplicate the stdin fd before Textual takes over the terminal.
    # Textual redirects its own input to /dev/tty when stdin is a pipe,
    # but os.dup ensures we hold a stable fd regardless.
    pipe_fd = os.dup(sys.stdin.fileno())

    app = SlackMonitorApp(
        config=config,
        llm=LLMClient(config),
        buffer=MessageBuffer(config),
        formatter=Formatter(config),
        channel=args.channel,
        pipe_fd=pipe_fd,
    )
    app.run(mouse=False)


def main() -> None:
    args = build_parser().parse_args()

    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        if args.no_tui:
            asyncio.run(_async_main(args))
        else:
            _run_tui(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
