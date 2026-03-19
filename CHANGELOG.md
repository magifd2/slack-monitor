# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-03-20

Initial release.

### Added

- **Core pipeline**: async reader → buffer → LLM → formatter architecture
  - `reader.py`: JSONL stdin parser producing `SlackMessage` objects
  - `buffer.py`: message accumulator with time / count / chars flush triggers
  - `analyzer.py`: asyncio task coordinator (ingest, tick, dispatch)
  - `llm.py`: OpenAI-compatible LLM client with full robustness chain
  - `formatter.py`: Rich terminal output for plain (`--no-tui`) mode
- **Textual TUI** (default mode): three-panel live display
  - System status panel: buffer count, next analysis countdown, LLM status
  - Analysis panel: topics, mood/activity, FINDINGS, SITUATION / THIS WIN
  - Message log panel: scrollable real-time message feed
- **FINDINGS**: cumulative, severity-tagged fact list persisted across analysis windows (`INFO` / `OK` / `WARN` / `ALRT` / `CRIT`)
- **SITUATION**: rolling cross-window situation synthesis with per-window delta (`THIS WIN`)
- **LLM robustness**: `<think>/<thinking>/<reasoning>` tag stripping, brace-depth JSON extraction, trailing-comma fix, summary-only regex fallback, never-crash fallback `AnalysisResult`
- **Context window management**: character-based token estimation, 180 K char hard ceiling, single-message truncation with `[TRUNCATED]` suffix
- **Prompt injection protection**: nonce-tagged `<messages-{nonce}>` wrapper with explicit injection warnings in system prompt; nonce rotated per LLM call
- **Security**: API keys via environment variables only (`SLACK_MONITOR_API_KEY` / `OPENAI_API_KEY`), no secrets in logs
- **Adaptive trigger**: analysis fires early when `trigger_messages` threshold is reached, without waiting for the full time window
- **Analysis language override**: `--language` flag (e.g. `--language Japanese`) to force output language; default follows message language automatically
- **Prior context**: last 3 analysis results are passed as context to the next LLM call for continuity
- **Live status bar** in plain mode: Rich `Live` display showing buffer state during message accumulation
- **CLI flags**: `--channel`, `--model`, `--window`, `--language`, `--show-raw`, `--no-tui`, `--debug`
- 114 unit tests; all LLM tests mock the `OpenAI` client (no real network calls)

### Fixed

- TUI startup crash on some terminals due to focus-event escape sequences leaking into display
- Pipe messages not reaching TUI: switched to thread-based reading with `asyncio.StreamReader` feed
- Mouse-tracking escape sequences left active after TUI exit
- Timestamps in message log converted to local timezone (was UTC)
- Analysis status stuck on "analyzing..." after LLM call completed
- Rich formatter output corrupting TUI stdout (formatter now writes to stderr in TUI mode)
- Messages stopping after first analysis due to task exception being silently swallowed
- `CancelledError` swallowed in `engine.run()` causing Ctrl+C to hang in TUI mode; final LLM flush is now skipped on cancellation
- Analysis panel timestamp range now reflects actual message timestamps (first → last post) instead of buffer wall-clock window times
