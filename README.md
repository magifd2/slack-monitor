# slack-monitor

Real-time Slack channel activity summarizer powered by a local or cloud LLM.

Monitors a Slack channel via [stail](https://github.com/magifd2/stail) and produces
periodic summaries of what's happening — topics, sentiment, cumulative findings, and
situation summary — rendered in a live TUI or printed as Rich panels.

## Two Commands

| Command | Mode | How it works |
|---------|------|--------------|
| `slack-monitor-tui` | TUI (default) | Spawns stail internally; live three-panel display |
| `slack-monitor` | Plain | Reads stail JSONL from stdin; prints Rich panels |

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

### TUI mode — `slack-monitor-tui`

Spawns stail internally. No pipe required.

```bash
# Basic usage
slack-monitor-tui --channel "#general"

# With extra stail arguments (e.g. custom config)
slack-monitor-tui --channel "#general" --stail-args "--config myconfig.json"

# Custom analysis window (30 seconds)
slack-monitor-tui --channel "#general" --window 30

# Force output language
slack-monitor-tui --channel "#general" --language Japanese

# Override LLM model
slack-monitor-tui --channel "#general" --model vertex_ai/gemini-2.0-flash

# Debug logging
slack-monitor-tui --channel "#general" --debug
```

stail is invoked as:
```
stail tail -f -q --format json --channel <channel> [stail-args...]
```

Press **Ctrl+C** to exit.

### Plain mode — `slack-monitor`

Reads stail JSONL from stdin. Pipe stail output manually.

```bash
# Basic usage
stail tail -f --format json -c "#general" | slack-monitor

# With channel label in output
stail tail -f --format json -c "#general" | slack-monitor --channel general

# Show raw LLM JSON output
stail tail -f --format json -c "#general" | slack-monitor --show-raw

# Debug mode
stail tail -f --format json -c "#general" | slack-monitor --debug
```

## Analysis Output

### TUI mode

Three-panel layout:

```
┌──────────────────────────────────────────────────────────────┐
│ slack-monitor  #general                          12:34:56    │  ← header
├─────────────────────┬────────────────────────────────────────┤
│ SYSTEM STATUS       │ 03/19 12:33:01 → 12:34:42  12 msgs     │
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

The analysis panel timestamp range reflects the actual message timestamps (first → last post in the window).

### Plain mode

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
