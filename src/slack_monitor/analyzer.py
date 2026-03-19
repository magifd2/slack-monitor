"""Central event loop coordinator: reader → buffer → LLM → formatter.

Two concurrent asyncio tasks drive the analysis loop:
- _ingest_task: reads messages from stdin and handles count/chars flushes
- _tick_task: drives time-based flushes

Both tasks enqueue FlushResult objects; _process_flush serializes LLM calls
to prevent overlapping requests.

Callbacks (all optional) allow the TUI to receive events without polling:
- on_message(msg): called for each buffered message
- on_analysis(result, reason): called after each analysis completes
- on_status(count, next_in_sec, llm_status): called on buffer state change
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from slack_monitor.buffer import FlushReason, FlushResult, MessageBuffer
from slack_monitor.formatter import Formatter, StatusBar
from slack_monitor.llm import LLMClient
from slack_monitor.models import AnalysisResult, AppConfig, SlackMessage
from slack_monitor.prompts import SYSTEM_PROMPT, build_user_prompt
from slack_monitor.reader import read_messages

_log = logging.getLogger(__name__)

OnMessageCb = Callable[[SlackMessage], None]
OnAnalysisCb = Callable[[AnalysisResult, FlushReason], None]
OnStatusCb = Callable[[int, int, str], None]  # (count, next_in_sec, llm_status)


class AnalyzerEngine:
    """Wires together all components for the real-time analysis pipeline."""

    def __init__(
        self,
        config: AppConfig,
        llm: LLMClient,
        buffer: MessageBuffer,
        formatter: Formatter,
        channel: str = "",
        on_message: Optional[OnMessageCb] = None,
        on_analysis: Optional[OnAnalysisCb] = None,
        on_status: Optional[OnStatusCb] = None,
        status_bar: Optional[StatusBar] = None,
    ) -> None:
        self._config = config
        self._llm = llm
        self._buffer = buffer
        self._formatter = formatter
        self._channel = channel
        self._on_message = on_message
        self._on_analysis = on_analysis
        self._on_status = on_status
        self._queue: asyncio.Queue[FlushResult] = asyncio.Queue()
        # StatusBar is used in no-tui mode; None disables it (TUI mode)
        self._status: Optional[StatusBar] = (
            status_bar if status_bar is not None else StatusBar(config.window_seconds)
        )
        self._window_start_dt: datetime = datetime.now(timezone.utc)

    def _next_in_sec(self) -> int:
        elapsed = (datetime.now(timezone.utc) - self._window_start_dt).seconds
        return max(0, self._config.window_seconds - elapsed)

    async def run(self, stream: asyncio.StreamReader) -> None:
        """Start the analysis pipeline."""
        if self._status is not None:
            self._status.start()

        ingest = asyncio.create_task(self._ingest_task(stream), name="ingest")
        tick = asyncio.create_task(self._tick_task(), name="tick")
        dispatch = asyncio.create_task(self._dispatch_task(), name="dispatch")

        try:
            await ingest
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _log.error("_ingest_task terminated with error: %s", e, exc_info=True)
        finally:
            tick.cancel()
            dispatch.cancel()
            await asyncio.gather(tick, dispatch, return_exceptions=True)
            if self._status is not None:
                self._status.stop()

            remaining = self._buffer.flush(FlushReason.TIME)
            if remaining:
                await self._process_flush(remaining)

    async def _ingest_task(self, stream: asyncio.StreamReader) -> None:
        """Read messages from stream and add to buffer."""
        async for msg in read_messages(stream):
            # Add to buffer; returns FlushResult if a threshold was hit
            try:
                flush_result = self._buffer.add(msg)
            except Exception as e:
                _log.error("buffer.add failed (skipping message): %s", e)
                continue

            if flush_result is not None:
                await self._queue.put(flush_result)
                # Note: on_message is NOT called for the message that triggered
                # the flush — it will appear in the analysis window instead.
            else:
                # Message buffered; notify TUI and status bar
                if self._on_message is not None:
                    try:
                        self._on_message(msg)
                    except Exception as e:
                        _log.error("on_message callback raised (ignoring): %s", e)
                preview = f"@{msg.user_name or msg.user_id}: {msg.text}"
                if self._status is not None:
                    self._status.update(self._buffer.count, preview)
                if self._on_status is not None:
                    try:
                        self._on_status(self._buffer.count, self._next_in_sec(), "waiting")
                    except Exception as e:
                        _log.error("on_status callback raised (ignoring): %s", e)

    async def _tick_task(self) -> None:
        """Periodically flush buffer based on window_seconds."""
        await self._buffer.ticker(self._queue)

    async def _dispatch_task(self) -> None:
        """Serialize LLM calls from the queue. Survives individual flush errors."""
        while True:
            result = await self._queue.get()
            try:
                await self._process_flush(result)
            except asyncio.CancelledError:
                self._queue.task_done()
                raise
            except Exception as e:
                # Log but keep the loop alive — don't let one bad flush kill all future analyses
                _log.error("_process_flush failed (continuing): %s", e, exc_info=True)
                # Reset status to waiting so the TUI doesn't stay stuck on "analyzing..."
                if self._on_status is not None:
                    try:
                        self._on_status(self._buffer.count, self._next_in_sec(), "waiting")
                    except Exception:
                        pass
            finally:
                self._queue.task_done()

    async def _process_flush(self, result: FlushResult) -> None:
        """Call LLM with flushed messages and display the analysis."""
        if not result.messages:
            return

        _log.info(
            "Analyzing %d messages (reason=%s, chars=%d)",
            len(result.messages),
            result.reason.value,
            sum(m.char_count() for m in result.messages),
        )

        if self._status is not None:
            self._status.set_analyzing()
        if self._on_status is not None:
            self._on_status(self._buffer.count, self._next_in_sec(), "analyzing")

        user_prompt = build_user_prompt(
            result.messages,
            result.window_start,
            result.window_end,
            channel_hint=self._channel,
        )

        # Use get_running_loop() — correct within an async context (Python 3.12+)
        loop = asyncio.get_running_loop()
        analysis, raw = await loop.run_in_executor(
            None,
            lambda: self._llm.analyze(SYSTEM_PROMPT, user_prompt),
        )

        if analysis is None:
            analysis = _make_fallback_analysis(result, raw)
        else:
            analysis = analysis.model_copy(
                update={
                    "window_start": result.window_start,
                    "window_end": result.window_end,
                    "message_count": len(result.messages),
                    "raw_llm_output": raw if self._config.show_raw else None,
                }
            )

        if self._on_analysis is not None:
            # TUI path: notify via callback (do NOT also write to stdout)
            try:
                self._on_analysis(analysis, result.reason)
            except Exception as e:
                _log.error("on_analysis callback raised: %s", e)
        else:
            # no-tui path: print Rich panel to stdout
            self._formatter.print_analysis(
                analysis,
                flush_reason=result.reason,
                channel=self._channel,
            )

        self._window_start_dt = datetime.now(timezone.utc)
        if self._status is not None:
            self._status.reset_window()
            self._status.update(self._buffer.count)
        if self._on_status is not None:
            try:
                self._on_status(self._buffer.count, self._next_in_sec(), "waiting")
            except Exception as e:
                _log.error("on_status callback raised: %s", e)


def _make_fallback_analysis(result: FlushResult, raw: str) -> AnalysisResult:
    """Create a minimal AnalysisResult when LLM parsing fails entirely."""
    _log.warning("LLM analysis failed; using fallback result")
    summary = raw[:500] if raw else "(no LLM output)"
    return AnalysisResult(
        window_start=result.window_start,
        window_end=result.window_end,
        message_count=len(result.messages),
        summary=f"[Analysis failed] Raw output: {summary}",
        raw_llm_output=raw,
    )
