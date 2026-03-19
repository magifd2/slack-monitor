"""Textual-based TUI for slack-monitor.

Three-panel layout:
┌──────────────────────────────────────────────────────┐
│ slack-monitor  #channel                              │ ← header
├────────────────────┬─────────────────────────────────┤
│  SYSTEM STATUS     │  LATEST ANALYSIS                │ ← top pane
│  Buffer  N msgs    │  topics, mood, events, summary  │
│  Next    Xs        │                                 │
│  Status  waiting   │                                 │
├────────────────────┴─────────────────────────────────┤
│  MESSAGE LOG (scrollable)                            │ ← log pane
│  HH:MM:SS @user  message text                       │
└──────────────────────────────────────────────────────┘
"""

import asyncio
import io
import os
import sys
import logging
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Footer, Header, RichLog, Static

from slack_monitor.buffer import FlushReason, MessageBuffer
from slack_monitor.formatter import Formatter
from slack_monitor.llm import LLMClient
from slack_monitor.models import AnalysisResult, AppConfig, SlackMessage

if TYPE_CHECKING:
    from slack_monitor.analyzer import AnalyzerEngine

_log = logging.getLogger(__name__)

_SENTIMENT_STYLE = {
    "positive": "green",
    "neutral": "dim",
    "negative": "red",
    "mixed": "yellow",
}

_ACTIVITY_STYLE = {
    "quiet": "dim",
    "normal": "green",
    "active": "yellow",
    "burst": "bold red",
}

_STATUS_LABEL = {
    "waiting": "[green]waiting[/green]",
    "analyzing": "[bold yellow]analyzing...[/bold yellow]",
}


class _MsgReceived(Message):
    def __init__(self, msg: SlackMessage) -> None:
        super().__init__()
        self.msg = msg


class _AnalysisReady(Message):
    def __init__(self, result: AnalysisResult, reason: FlushReason) -> None:
        super().__init__()
        self.result = result
        self.reason = reason


class _StatusUpdate(Message):
    def __init__(self, count: int, next_in_sec: int, llm_status: str) -> None:
        super().__init__()
        self.count = count
        self.next_in_sec = next_in_sec
        self.llm_status = llm_status


class SlackMonitorApp(App):
    """Main TUI application for slack-monitor."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #top-pane {
        layout: horizontal;
        height: 40%;
        min-height: 10;
    }

    #status-panel {
        width: 26;
        border-right: solid $primary-darken-2;
        padding: 1 1;
        overflow: hidden auto;
        background: $surface;
    }

    #analysis-panel {
        width: 1fr;
        padding: 1 1;
        overflow: hidden auto;
        background: $surface;
    }

    #log-panel {
        height: 1fr;
        border-top: solid $primary-darken-2;
        background: $surface;
        padding: 0 1;
    }

    Header {
        background: $primary;
        color: $text;
    }
    """

    def __init__(
        self,
        config: AppConfig,
        llm: LLMClient,
        buffer: MessageBuffer,
        formatter: Formatter,
        channel: str = "",
        pipe_fd: int | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._llm = llm
        self._buffer = buffer
        self._formatter = formatter
        self._channel = channel
        self._pipe_fd = pipe_fd  # pre-duplicated stdin fd

    def compose(self) -> ComposeResult:
        title = f"slack-monitor  {'#' + self._channel if self._channel else ''}"
        yield Header(show_clock=True)
        self.title = title
        with Horizontal(id="top-pane"):
            yield Static(_render_status(0, self._config.window_seconds, "waiting"), id="status-panel")
            yield Static(_render_no_analysis(), id="analysis-panel")
        yield RichLog(id="log-panel", highlight=True, markup=True, auto_scroll=True)

    def on_mount(self) -> None:
        self.run_worker(self._run_engine(), exclusive=True, thread=False)

    async def _run_engine(self) -> None:
        """Start the analyzer pipeline inside Textual's event loop."""
        from slack_monitor.analyzer import AnalyzerEngine

        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)

        if self._pipe_fd is not None:
            # Use the pre-duplicated fd (Textual may have taken over sys.stdin)
            pipe_file = open(self._pipe_fd, "rb", buffering=0)
            await loop.connect_read_pipe(lambda: protocol, pipe_file)
        else:
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        engine = AnalyzerEngine(
            config=self._config,
            llm=self._llm,
            buffer=self._buffer,
            formatter=self._formatter,
            channel=self._channel,
            on_message=self._cb_message,
            on_analysis=self._cb_analysis,
            on_status=self._cb_status,
            status_bar=None,  # disable stderr StatusBar in TUI mode
        )

        await engine.run(reader)
        # stdin EOF → graceful shutdown
        self.exit()

    # --- Callbacks (called from engine coroutines on the same event loop) ---

    def _cb_message(self, msg: SlackMessage) -> None:
        self.post_message(_MsgReceived(msg))

    def _cb_analysis(self, result: AnalysisResult, reason: FlushReason) -> None:
        self.post_message(_AnalysisReady(result, reason))

    def _cb_status(self, count: int, next_in_sec: int, llm_status: str) -> None:
        self.post_message(_StatusUpdate(count, next_in_sec, llm_status))

    # --- Message handlers (UI fiber) ---

    def on__msg_received(self, event: _MsgReceived) -> None:
        log = self.query_one("#log-panel", RichLog)
        msg = event.msg
        ts = _short_time(msg.timestamp)
        user = f"@{msg.user_name or msg.user_id or '?'}"
        bot_tag = " [dim][bot][/dim]" if msg.post_type.value == "bot" else ""
        reply_tag = " [dim](reply)[/dim]" if msg.is_reply else ""
        log.write(
            f"[dim]{ts}[/dim] [bold cyan]{user}[/bold cyan]{bot_tag}{reply_tag}  {msg.text}"
        )

    def on__analysis_ready(self, event: _AnalysisReady) -> None:
        panel = self.query_one("#analysis-panel", Static)
        panel.update(_render_analysis(event.result))

    def on__status_update(self, event: _StatusUpdate) -> None:
        panel = self.query_one("#status-panel", Static)
        panel.update(
            _render_status(event.count, event.next_in_sec, event.llm_status)
        )


# --- Pure render helpers (no widget state) ---

def _render_status(count: int, next_in_sec: int, llm_status: str) -> str:
    status_label = _STATUS_LABEL.get(llm_status, llm_status)
    return (
        "[bold]SYSTEM STATUS[/bold]\n\n"
        f"Buffer  [bold]{count}[/bold] msgs\n"
        f"Next    [cyan]{next_in_sec}s[/cyan]\n"
        f"Status  {status_label}"
    )


def _render_no_analysis() -> str:
    return "[dim]Waiting for first analysis window...[/dim]"


def _render_analysis(result: AnalysisResult) -> str:
    start = _short_time(result.window_start)
    end = _short_time(result.window_end)

    sentiment_style = _SENTIMENT_STYLE.get(result.sentiment, "")
    activity_style = _ACTIVITY_STYLE.get(result.activity_level.value, "")

    lines = [
        f"[bold]{start} → {end}[/bold]  "
        f"[dim]{result.message_count} msgs[/dim]  "
        f"[{activity_style}]{result.activity_level.value}[/{activity_style}]",
        "",
    ]

    if result.topics:
        lines.append(f"[bold]TOPICS[/bold]  {', '.join(result.topics)}")

    lines.append(
        f"[bold]MOOD  [/bold]  [{sentiment_style}]{result.sentiment}[/{sentiment_style}]"
    )

    if result.key_events:
        lines.append("")
        lines.append("[bold]EVENTS[/bold]")
        for ev in result.key_events:
            lines.append(f"  • {ev}")

    if result.summary:
        lines.append("")
        lines.append(result.summary)

    return "\n".join(lines)


def _short_time(ts: str) -> str:
    """Extract HH:MM:SS from RFC3339 or return as-is."""
    if "T" in ts:
        time_part = ts.split("T", 1)[1]
        return time_part.split("Z")[0].split("+")[0].split("-")[0][:8]
    return ts[:8]
