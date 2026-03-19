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

TUI mode spawns stail internally as a subprocess:
    stail tail -f -q --format json --channel <channel> [extra_stail_args...]

This avoids all stdin/pipe conflicts with Textual's terminal management.
"""

import asyncio
import logging
import shlex
import traceback
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Header, RichLog, Static
from textual.worker import Worker, WorkerState

from slack_monitor.buffer import FlushReason, MessageBuffer
from slack_monitor.formatter import Formatter
from slack_monitor.llm import LLMClient
from slack_monitor.models import AnalysisResult, AppConfig, FindingSeverity, SlackMessage

_log = logging.getLogger(__name__)

_FINDING_TAG = {
    FindingSeverity.INFO:     ("INFO", "dim"),
    FindingSeverity.POSITIVE: ("OK  ", "green"),
    FindingSeverity.WARNING:  ("WARN", "yellow"),
    FindingSeverity.NEGATIVE: ("ALRT", "red"),
    FindingSeverity.CRITICAL: ("CRIT", "bold red"),
}

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

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    CSS = """
    Screen {
        layout: vertical;
    }

    #top-pane {
        layout: horizontal;
        height: 2fr;
        min-height: 14;
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
        stail_args: str = "",
    ) -> None:
        super().__init__()
        self._config = config
        self._llm = llm
        self._buffer = buffer
        self._formatter = formatter
        self._channel = channel
        self._stail_args = stail_args
        self._stail_proc: asyncio.subprocess.Process | None = None

    def compose(self) -> ComposeResult:
        self.title = f"slack-monitor  {'#' + self._channel if self._channel else ''}"
        yield Header(show_clock=True)
        with Horizontal(id="top-pane"):
            yield Static(
                _render_status(0, self._config.window_seconds, "waiting"),
                id="status-panel",
            )
            yield Static(_render_no_analysis(), id="analysis-panel")
        yield RichLog(id="log-panel", highlight=True, markup=True, auto_scroll=True)

    def on_mount(self) -> None:
        self.run_worker(self._run_engine(), exclusive=True, exit_on_error=False)

    async def _run_engine(self) -> None:
        """Spawn stail and run the analyzer pipeline inside Textual's event loop."""
        log = self.query_one("#log-panel", RichLog)
        try:
            from slack_monitor.analyzer import AnalyzerEngine

            # Build stail command
            cmd = [
                "stail", "tail", "-f", "-q",
                "--format", "json",
                "--channel", self._channel,
            ]
            if self._stail_args:
                cmd.extend(shlex.split(self._stail_args))

            _log.debug("Spawning stail: %s", " ".join(cmd))

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                log.write("[bold red]ERROR:[/bold red] 'stail' not found on PATH.")
                log.write("[dim]Install stail or check your PATH.[/dim]")
                return
            except OSError as e:
                log.write(f"[bold red]ERROR:[/bold red] Failed to start stail: {e}")
                return

            self._stail_proc = proc
            assert proc.stdout is not None
            assert proc.stderr is not None
            reader: asyncio.StreamReader = proc.stdout

            # Relay stail stderr to the log panel so startup errors are visible.
            async def _relay_stderr() -> None:
                async for line in proc.stderr:
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        log.write(f"[dim yellow][stail] {text}[/dim yellow]")

            stderr_task = asyncio.create_task(_relay_stderr(), name="stail-stderr")

            engine = AnalyzerEngine(
                config=self._config,
                llm=self._llm,
                buffer=self._buffer,
                formatter=self._formatter,
                channel=self._channel,
                on_message=self._cb_message,
                on_analysis=self._cb_analysis,
                on_status=self._cb_status,
                status_bar=None,  # stderr StatusBar disabled in TUI mode
            )

            await engine.run(reader)
            stderr_task.cancel()

            rc = proc.returncode
            if rc is not None and rc != 0:
                log.write(f"[bold red]stail exited with code {rc}[/bold red]")
                log.write("[dim]Check the command above. Press Ctrl+C to quit.[/dim]")
                return  # stay open so user can read the error

            # stail exited cleanly (EOF) — exit TUI too
            self.exit()

        except asyncio.CancelledError:
            pass  # Normal shutdown (Ctrl+C)
        except Exception as e:
            log.write(f"[bold red]ERROR:[/bold red] {e}")
            for line in traceback.format_exc().splitlines():
                log.write(f"[dim]{line}[/dim]")
            _log.exception("Engine crashed")
        finally:
            await self._terminate_stail()

    async def _terminate_stail(self) -> None:
        """Gracefully terminate the stail subprocess."""
        proc = self._stail_proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        except Exception as e:
            _log.warning("Error terminating stail: %s", e)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.ERROR:
            log = self.query_one("#log-panel", RichLog)
            log.write(f"[bold red]Worker failed:[/bold red] {event.worker}")

    # --- Callbacks (called from engine coroutines — same event loop as app) ---

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
        ts = _local_datetime(msg.timestamp)
        user = f"@{msg.user_name or msg.user_id or '?'}"
        bot_tag = " [dim][bot][/dim]" if msg.post_type.value == "bot" else ""
        reply_tag = " [dim](reply)[/dim]" if msg.is_reply else ""
        log.write(
            f"[dim]{ts}[/dim] [bold cyan]{user}[/bold cyan]{bot_tag}{reply_tag}  {msg.text}"
        )

    def on__analysis_ready(self, event: _AnalysisReady) -> None:
        self.query_one("#analysis-panel", Static).update(
            _render_analysis(event.result)
        )

    def on__status_update(self, event: _StatusUpdate) -> None:
        self.query_one("#status-panel", Static).update(
            _render_status(event.count, event.next_in_sec, event.llm_status)
        )


# --- Pure render helpers ---

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
    start = _local_datetime(result.window_start)
    end = _local_datetime(result.window_end)

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
        f"   [{activity_style}]{result.activity_level.value}[/{activity_style}]"
    )
    if result.findings:
        lines.append("")
        lines.append("[bold yellow]FINDINGS[/bold yellow]")
        for finding in result.findings:
            tag, style = _FINDING_TAG.get(finding.severity, ("INFO", "dim"))
            lines.append(f"  \\[[{style}]{tag}[/{style}]] {finding.text}")
    if result.key_events:
        lines.append("")
        lines.append("[bold]NEW[/bold]")
        for ev in result.key_events:
            lines.append(f"  • {ev}")
    if result.ongoing_summary:
        lines.append("")
        lines.append("[bold cyan]SITUATION[/bold cyan]")
        lines.append(result.ongoing_summary)
        if result.summary and result.summary != result.ongoing_summary:
            lines.append("")
            lines.append(f"[dim][bold]THIS WIN[/bold]  {result.summary}[/dim]")
    elif result.summary:
        lines.append("")
        lines.append(result.summary)

    return "\n".join(lines)


def _local_datetime(ts: str) -> str:
    """Convert RFC3339/UTC timestamp to local date+time string."""
    if not ts:
        return ""
    try:
        if ts.endswith("Z"):
            dt = datetime.fromisoformat(ts[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(ts)
        return dt.astimezone().strftime("%m/%d %H:%M:%S")
    except (ValueError, TypeError):
        if "T" in ts:
            return ts.split("T", 1)[1][:8]
        return ts[:19]
