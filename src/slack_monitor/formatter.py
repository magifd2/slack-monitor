"""Rich-based terminal formatter for analysis results."""

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from slack_monitor.buffer import FlushReason
from slack_monitor.models import ActivityLevel, AnalysisResult, AppConfig

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

_FLUSH_REASON_LABELS = {
    FlushReason.TIME: "⏱ time",
    FlushReason.COUNT: "# count",
    FlushReason.CHARS: "⚠ chars",
}


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

        # Topics
        if result.topics:
            t.append("TOPICS:  ", style="bold")
            t.append(", ".join(result.topics))
            t.append("\n")

        # Sentiment
        sentiment_color = _SENTIMENT_COLORS.get(result.sentiment, "")
        t.append("MOOD:    ", style="bold")
        t.append(result.sentiment, style=sentiment_color)
        t.append("\n")

        # Key events
        if result.key_events:
            t.append("EVENTS:", style="bold")
            t.append("\n")
            for event in result.key_events:
                t.append(f"  • {event}\n")

        # Summary
        if result.summary:
            if result.topics or result.key_events:
                t.append("\n")
            t.append(result.summary)

        return t
