# slack-monitor

Real-time Slack channel activity summarizer powered by a local or cloud LLM.

Reads Slack messages streamed by [stail](https://github.com/magifd2/stail) and produces
periodic summaries of what's happening in the channel — topics, sentiment, cumulative
findings, and situation summary — rendered in a live TUI.

## How It Works

```
stail tail -f --format json -c "#general" | slack-monitor --channel general
```

1. `stail` streams Slack messages as JSONL to stdout
2. `slack-monitor` reads stdin, accumulates messages in a time/count window
3. Each window is sent to an LLM (local or cloud, OpenAI-compatible API)
4. The LLM returns structured analysis (JSON) including cumulative findings that persist across windows
5. Results are displayed in a three-panel TUI (status / analysis / message log)

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [stail](https://github.com/magifd2/stail) (configured and on PATH)
- An OpenAI-compatible LLM API:
  - [LM Studio](https://lmstudio.ai/) (local, recommended for development)
  - [litellm-proxy](https://docs.litellm.ai/docs/proxy/quick_start) (for cloud models via Vertex AI etc.)

## Installation

```bash
git clone <repo>
cd slack-monitor
uv sync --all-groups
```

## Configuration

### 1. Set your API key (required)

```bash
export SLACK_MONITOR_API_KEY="lm-studio"   # for LM Studio
export SLACK_MONITOR_API_KEY="your-key"    # for cloud APIs
```

Or use `OPENAI_API_KEY` as a fallback.

> **Security:** Never put the API key in `config.toml`. Always use environment variables.

### 2. Create a config file (optional)

```bash
cp config.toml.example config.toml
# Edit config.toml as needed
```

Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `[llm] base_url` | `http://localhost:1234/v1` | LLM API endpoint |
| `[llm] model` | `openai/gpt-oss-20b` | Model identifier |
| `[buffer] window_seconds` | `60` | Analysis interval in seconds |
| `[buffer] max_messages` | `50` | Max messages per analysis batch |
| `[output] show_raw` | `false` | Show raw LLM JSON output |

All settings can be overridden via environment variables (see `.env.example`).

### 3. LM Studio setup

1. Download and start LM Studio
2. Load model `openai/gpt-oss-20b` (or any compatible model)
3. Start the local server (default: `http://localhost:1234`)

### 4. litellm-proxy setup (for Vertex AI Gemini)

```bash
litellm --model vertex_ai/gemini-2.0-flash --port 4000
```

Then set:
```bash
export SLACK_MONITOR_BASE_URL="http://localhost:4000/v1"
export SLACK_MONITOR_MODEL="vertex_ai/gemini-2.0-flash"
```

## Usage

```bash
# TUI mode (default) — three-panel live display
stail tail -f --format json -c "#general" | slack-monitor --channel general

# Plain mode — Rich panels printed to stdout
stail tail -f --format json -c "#general" | slack-monitor --no-tui

# Custom window (30 seconds)
stail tail -f --format json -c "#general" | slack-monitor --window 30

# Force output language
stail tail -f --format json -c "#general" | slack-monitor --language Japanese

# Debug mode with raw LLM output
stail tail -f --format json -c "#general" | slack-monitor --debug --show-raw

# Override model
stail tail -f --format json -c "#general" | slack-monitor --model vertex_ai/gemini-2.0-flash
```

## Analysis Output

### TUI mode (default)

Three-panel layout:

```
┌──────────────────────────────────────────────────────────────┐
│ slack-monitor  #general                          12:34:56    │  ← header
├─────────────────────┬────────────────────────────────────────┤
│ SYSTEM STATUS       │ 2026-03-19 12:33 → 12:34  12 msgs      │
│                     │                                        │
│ Buffer  0 msgs      │ TOPICS  deployment, rollback           │
│ Next    45s         │ MOOD    negative   active              │
│ Status  waiting     │                                        │
│                     │ FINDINGS                               │
│                     │   [CRIT] Deploy #347 failed at 12:33   │
│                     │   [WARN] Rollback in progress          │
│                     │                                        │
│                     │ SITUATION                              │
│                     │ Production deploy failed; team is      │
│                     │ executing rollback procedure.          │
├─────────────────────┴────────────────────────────────────────┤
│ 12:33:01 @alice  deploy #347 started                        │
│ 12:33:42 @bob    it's failing, initiating rollback          │
└──────────────────────────────────────────────────────────────┘
```

**FINDINGS** is a cumulative list of concrete facts maintained across analysis windows.
Each finding has a severity tag: `[INFO]` `[OK]` `[WARN]` `[ALRT]` `[CRIT]`.

**SITUATION** is a rolling synthesis of the overall picture across all recent windows.
**THIS WIN** (shown below SITUATION when different) describes only what changed in the current window.

### Plain mode (`--no-tui`)

Each analysis window is printed as a Rich panel to stdout.

## Development

```bash
# Run tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run a single test file
uv run pytest tests/test_llm.py
```

See [CLAUDE.md](CLAUDE.md) for detailed development rules and architecture documentation.

## LLM Robustness

slack-monitor is designed to work reliably with local LLMs that may:
- Produce `<think>/<thinking>` blocks (stripped automatically)
- Return malformed JSON (brace-depth extraction + trailing-comma fix)
- Use inconsistent formatting (multi-strategy fallback chain)
- Have limited context windows (64K token budget management)

## Security

- API keys are never stored in config files or code
- Slack message content is treated as untrusted input and wrapped in injection-protected tags
- URLs in messages are defanged (`http://` → `hxxp://`) before being sent to the LLM or displayed
- LLM output is instructed to defang any domain or URL it mentions in findings/summaries
- Input validation at all system boundaries (stdin, LLM output, config)
- See [CLAUDE.md](CLAUDE.md) for full security policy
