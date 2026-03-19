"""Rich-based terminal formatter for analysis results."""

import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from slack_monitor.buffer import FlushReason
from slack_monitor.models import ActivityLevel, AnalysisResult, AppConfig, FindingSeverity

_ACTIVITY_COLORS = {
    ActivityLevel.QUIET: "dim",
    ActivityLevel.NORMAL: "green",
    ActivityLevel.ACTIVE: "yellow",
    ActivityLevel.BURST: "bold red",
}

_SENTIMENT_COLORS = {
    "positive": "green",
    "neutral": "dim",
    "negative": "red",
    "mixed": "yellow",
}

_FINDING_TAG = {
    FindingSeverity.INFO:     ("[INFO]",  "dim"),
    FindingSeverity.POSITIVE: ("[OK]  ",  "green"),
    FindingSeverity.WARNING:  ("[WARN]",  "yellow"),
    FindingSeverity.NEGATIVE: ("[ALRT]",  "red"),
    FindingSeverity.CRITICAL: ("[CRIT]",  "bold red"),
}

_FLUSH_REASON_LABELS = {
    FlushReason.TIME: "⏱ time",
    FlushReason.COUNT: "# count",
    FlushReason.CHARS: "⚠ chars",
}


def _short_time(ts: str) -> str:
    """Convert RFC3339/UTC timestamp to local HH:MM string."""
    if not ts:
        return ""
    try:
        if ts.endswith("Z"):
            dt = datetime.fromisoformat(ts[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(ts)
        return dt.astimezone().strftime("%H:%M")
    except (ValueError, TypeError):
        if "T" in ts:
            return ts.split("T", 1)[1][:5]
        return ts[:5]


class StatusBar:
    """Live status bar shown while buffering messages (stderr)."""

    def __init__(self, window_seconds: int) -> None:
        self._window_seconds = window_seconds
        self._count = 0
        self._last_preview = ""
        self._analyzing = False
        self._window_start: datetime = datetime.now(timezone.utc)
        self._console = Console(stderr=True)
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=4,
            transient=True,
        )

    def start(self) -> None:
        self._live.start()

    def stop(self) -> None:
        self._live.stop()

    def update(self, count: int, last_preview: str = "") -> None:
        self._count = count
        if last_preview:
            self._last_preview = last_preview
        self._analyzing = False
        self._live.update(self._render())

    def set_analyzing(self) -> None:
        self._analyzing = True
        self._live.update(self._render())

    def reset_window(self) -> None:
        self._window_start = datetime.now(timezone.utc)
        self._count = 0
        self._last_preview = ""

    def _render(self) -> Text:
        elapsed = (datetime.now(timezone.utc) - self._window_start).seconds
        remaining = max(0, self._window_seconds - elapsed)

        t = Text()
        if self._analyzing:
            t.append("⚙ ", style="yellow")
            t.append("Analyzing...", style="bold yellow")
        else:
            t.append("● ", style="green")
            t.append(f"{self._count} msgs buffered", style="bold")
            t.append(f"  next analysis in ", style="dim")
            t.append(f"{remaining}s", style="cyan")
            if self._last_preview:
                preview = self._last_preview[:60].replace("\n", " ")
                t.append(f"  └ {preview}", style="dim")
        return t


class Formatter:
    """Renders AnalysisResult to terminal using Rich."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._console = Console()

    def print_analysis(
        self,
        result: AnalysisResult,
        flush_reason: FlushReason = FlushReason.TIME,
        channel: str = "",
    ) -> None:
        """Render one analysis window as a Rich panel to stdout."""
        title = self._build_title(result, flush_reason, channel)
        body = self._build_body(result)

        self._console.print(
            Panel(body, title=title, border_style="blue", padding=(0, 1))
        )

        if self._config.show_raw and result.raw_llm_output:
            self._console.print(
                Panel(
                    result.raw_llm_output,
                    title="[dim]Raw LLM output[/dim]",
                    border_style="dim",
                    padding=(0, 1),
                )
            )

    def _build_title(
        self,
        result: AnalysisResult,
        flush_reason: FlushReason,
        channel: str,
    ) -> str:
        parts = []
        if channel:
            parts.append(f"#{channel}")
        start = _short_time(result.window_start)
        end = _short_time(result.window_end)
        parts.append(f"{start} → {end}")
        parts.append(f"{result.message_count} msgs")
        activity_color = _ACTIVITY_COLORS.get(result.activity_level, "")
        parts.append(f"[{activity_color}]{result.activity_level.value}[/{activity_color}]")
        reason_label = _FLUSH_REASON_LABELS.get(flush_reason, "")
        if reason_label:
            parts.append(f"[dim]{reason_label}[/dim]")
        return "  ".join(parts)

    def _build_body(self, result: AnalysisResult) -> Text:
        t = Text()

        # Topics / mood (compact header line)
        sentiment_color = _SENTIMENT_COLORS.get(result.sentiment, "")
        activity_color = _ACTIVITY_COLORS.get(result.activity_level, "")
        if result.topics:
            t.append("TOPICS:    ", style="bold")
            t.append(", ".join(result.topics))
            t.append("\n")
        t.append("MOOD:      ", style="bold")
        t.append(result.sentiment, style=sentiment_color)
        t.append("   ")
        t.append(result.activity_level.value, style=activity_color)
        t.append("\n")

        # Cumulative findings — the "what we know" list
        if result.findings:
            t.append("\n")
            t.append("FINDINGS:", style="bold yellow")
            t.append("\n")
            for finding in result.findings:
                tag, style = _FINDING_TAG.get(finding.severity, ("[INFO]", "dim"))
                t.append(f"  {tag} ", style=style)
                t.append(finding.text)
                t.append("\n")

        # New events this window
        if result.key_events:
            t.append("\n")
            t.append("NEW:", style="bold")
            t.append("\n")
            for event in result.key_events:
                t.append(f"  • {event}")
                t.append("\n")

        # Ongoing situation prose
        if result.ongoing_summary:
            t.append("\n")
            t.append("SITUATION: ", style="bold cyan")
            t.append(result.ongoing_summary)
        elif result.summary:
            t.append("\n")
            t.append(result.summary)

        if result.ongoing_summary and result.summary and result.summary != result.ongoing_summary:
            t.append("\n\n")
            t.append("THIS WIN:  ", style="bold dim")
            t.append(result.summary, style="dim")

        return t
