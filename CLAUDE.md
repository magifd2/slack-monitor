# slack-monitor — Development Rules

This file defines the rules and conventions for developing and maintaining this project.
It is enforced in all Claude Code sessions working in this repository.

## Project Overview

slack-monitor monitors Slack channel messages via [stail](https://github.com/magifd2/stail)
and uses a local or cloud LLM (OpenAI-compatible API) to produce real-time summaries of
channel activity.

Two entry points:
- **`slack-monitor-tui`** — TUI mode. Spawns stail internally as a subprocess.
- **`slack-monitor`** — Plain mode. Reads stail JSONL from stdin (pipe).

## Development Rules

### Security First
- **API keys MUST NOT appear in code, config files, or git history.** Use environment
  variables (`SLACK_MONITOR_API_KEY` or `OPENAI_API_KEY`) only.
- **Prompt injection protection is required.** User-sourced text (Slack message content)
  must always be wrapped in `<messages>` tags with explicit injection warnings in the
  system prompt.
- **Input validation is mandatory** at all system boundaries (stdin JSON parsing, LLM
  output parsing, config file loading).
- **No secrets in logs.** API keys, tokens, and user message content must not appear
  in log output at WARNING level or above.

### Code Quality
- **Small implementations, small fixes.** Prefer focused changes. If a file exceeds ~300
  lines, consider splitting by responsibility.
- **Testable structure.** Every module must be independently testable without network
  calls. Use dependency injection for the LLM client.
- **No silent failures.** All errors must be logged at WARNING or higher. Failed LLM
  parses produce a fallback result, not a crash.

### Docs and Code Sync
- Update `README.md` whenever user-facing behavior changes.
- Update `CLAUDE.md` when development rules change.
- Update `config.toml.example` and `.env.example` when new configuration keys are added.
- All changes go to git; no undocumented drifts from the config schema.

### LLM Robustness
- Always strip `<think>/<thinking>/<reasoning>` tags before parsing LLM output.
- Use brace-depth tracking for JSON extraction (handles markdown fences and preambles).
- Implement a fallback chain: direct parse → extracted block → trailing-comma fix →
  summary-only regex → fallback AnalysisResult (never crash).
- Temperature should be ≤ 0.3 in defaults to maximize structural consistency.

### Context Window Management
- Default `max_chars = 180_000` (~45K tokens) is the hard ceiling for the message buffer.
  This leaves ~19K tokens headroom for system prompt + output within a 64K context limit.
- Character-based estimation is used (no tokenizer dependency):
  `token_estimate ≈ char_count // 3` (pessimistic, safe for Japanese/CJK).
- Single oversized messages are truncated with `[TRUNCATED]` suffix and a WARNING log.

### Testing
- Run tests with: `uv run pytest`
- All tests must pass before committing.
- New features require corresponding tests.
- Tests must not make real network calls. Mock the `OpenAI` client in all LLM tests.
- Test file naming: `test_<module_name>.py` in `tests/`.

### Git
- Commit early and often. Each commit should represent a coherent, working state.
- Commit messages: imperative mood, ≤ 72 chars subject line, body for context.
- Never commit: `.env`, `config.toml`, secrets, or large binary files.

## Architecture

### TUI mode (`slack-monitor-tui`)

```
stail subprocess (spawned internally via asyncio.create_subprocess_exec)
    ↓  stdout as asyncio.StreamReader
reader.py       # Parse JSONL → SlackMessage
    ↓
buffer.py       # Accumulate with time/count/chars triggers → FlushResult
    ↓
analyzer.py     # Coordinate pipeline (asyncio tasks)
    ↓
llm.py          # LLM call with robustness: strip think, extract JSON, fallback
    ↓
tui.py          # Textual three-panel display (callbacks from analyzer)
```

stail is invoked as:
```
stail tail -f -q --format json --channel <channel> [--stail-args ...]
```

### Plain mode (`slack-monitor`)

```
stdin (stail JSONL piped externally)
    ↓
reader.py       # Parse JSONL → SlackMessage
    ↓
buffer.py       # Accumulate with time/count/chars triggers → FlushResult
    ↓
analyzer.py     # Coordinate pipeline (asyncio tasks)
    ↓
llm.py          # LLM call with robustness: strip think, extract JSON, fallback
    ↓
formatter.py    # Rich terminal output (panels to stdout)
```

## LLM Integration Notes

### LM Studio (local)
- base_url: `http://localhost:1234/v1`
- api_key: any non-empty string (e.g. `lm-studio`)
- Model: `openai/gpt-oss-20b`
- Context limit: ~64K tokens

### litellm-proxy (Vertex AI Gemini)
- base_url: `http://localhost:4000/v1` (or your proxy address)
- api_key: your litellm proxy master key
- Model: e.g. `vertex_ai/gemini-2.0-flash`
- Thinking models (Gemini 2.0+): `<think>` output is handled automatically

## Commands

```bash
# Install dependencies
uv sync --all-groups

# Run tests
uv run pytest

# TUI mode (spawns stail internally)
uv run slack-monitor-tui --channel "#general"
uv run slack-monitor-tui --channel "#general" --stail-args "--config myconfig.json"
uv run slack-monitor-tui --channel "#general" --debug

# Plain mode (pipe from stail)
stail tail -f --format json -c "#general" | uv run slack-monitor
stail tail -f --format json -c "#general" | uv run slack-monitor --debug --show-raw

# Show help
uv run slack-monitor-tui --help
uv run slack-monitor --help
```
