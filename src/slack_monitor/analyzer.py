"""Central event loop coordinator: reader → buffer → LLM → formatter.

Two concurrent asyncio tasks drive the analysis loop:
- _ingest_task: reads messages from stdin and handles count/chars flushes
- _tick_task: drives time-based flushes

Both tasks enqueue FlushResult objects; _process_flush serializes LLM calls
to prevent overlapping requests.
"""

import asyncio
import logging
from datetime import timezone

from slack_monitor.buffer import FlushReason, FlushResult, MessageBuffer
from slack_monitor.formatter import Formatter
from slack_monitor.llm import LLMClient
from slack_monitor.models import AnalysisResult, AppConfig
from slack_monitor.prompts import SYSTEM_PROMPT, build_user_prompt
from slack_monitor.reader import read_messages

_log = logging.getLogger(__name__)


class AnalyzerEngine:
    """Wires together all components for the real-time analysis pipeline."""

    def __init__(
        self,
        config: AppConfig,
        llm: LLMClient,
        buffer: MessageBuffer,
        formatter: Formatter,
        channel: str = "",
    ) -> None:
        self._config = config
        self._llm = llm
        self._buffer = buffer
        self._formatter = formatter
        self._channel = channel
        self._queue: asyncio.Queue[FlushResult] = asyncio.Queue()

    async def run(self, stream: asyncio.StreamReader) -> None:
        """Start the analysis pipeline.

        Runs three concurrent tasks:
        - _ingest_task: reads messages and handles count/chars flushes
        - _tick_task: drives time-based flushes
        - _dispatch_task: serializes LLM calls from the queue
        """
        ingest = asyncio.create_task(self._ingest_task(stream), name="ingest")
        tick = asyncio.create_task(self._tick_task(), name="tick")
        dispatch = asyncio.create_task(self._dispatch_task(), name="dispatch")

        try:
            # Wait for ingest to finish (stdin EOF), then cancel the rest
            await ingest
        except asyncio.CancelledError:
            pass
        finally:
            tick.cancel()
            dispatch.cancel()
            await asyncio.gather(tick, dispatch, return_exceptions=True)

            # Flush any remaining messages
            remaining = self._buffer.flush(FlushReason.TIME)
            if remaining:
                await self._process_flush(remaining)

    async def _ingest_task(self, stream: asyncio.StreamReader) -> None:
        """Read messages from stream and add to buffer."""
        async for msg in read_messages(stream):
            flush_result = self._buffer.add(msg)
            if flush_result is not None:
                await self._queue.put(flush_result)

    async def _tick_task(self) -> None:
        """Periodically flush buffer based on window_seconds."""
        await self._buffer.ticker(self._queue)

    async def _dispatch_task(self) -> None:
        """Serialize LLM calls from the queue."""
        while True:
            result = await self._queue.get()
            try:
                await self._process_flush(result)
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

        user_prompt = build_user_prompt(
            result.messages,
            result.window_start,
            result.window_end,
            channel_hint=self._channel,
        )

        # Run LLM call in thread pool to avoid blocking the event loop
        analysis, raw = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._llm.analyze(SYSTEM_PROMPT, user_prompt),
        )

        if analysis is None:
            analysis = _make_fallback_analysis(result, raw)
        else:
            # Fill in window metadata that LLM doesn't know
            analysis = analysis.model_copy(
                update={
                    "window_start": result.window_start,
                    "window_end": result.window_end,
                    "message_count": len(result.messages),
                    "raw_llm_output": raw if self._config.show_raw else None,
                }
            )

        self._formatter.print_analysis(
            analysis,
            flush_reason=result.reason,
            channel=self._channel,
        )


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
