# AI Loop

CLI orchestrator for automated Linear issue implementation with AI-powered planning and critique.

```
Linear Issues → Claude Plans → ChatGPT Critique → Claude Implementation
```

## Features

- **Linear Integration**: Fetch issues by identifier or query by team/project/state/label
- **Two-Stage Gating**: PLAN_GATE and CODE_GATE ensure quality at each phase
- **Claude Builder**: Generate, refine, and implement plans using Claude Code CLI
- **ChatGPT Critic**: Rigorous critique with structured JSON output via Codex CLI
- **Git Isolation**: Work in branches or worktrees to avoid conflicts
- **Web Dashboard**: Real-time monitoring with expandable timeline, markdown rendering
- **Live Terminal UI**: Rich terminal UI for batch runs with progress tracking
- **Artifact Logging**: Full trace of each run for debugging and auditing

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/ArchieOS-org/ai-loop.git
cd ai-loop/ai-loop
uv venv && source .venv/bin/activate
uv pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env with your LINEAR_API_KEY

# 3. Run web dashboard
ai-loop dev --port 8080
```

## Installation

### Prerequisites

Before installing AI Loop, ensure you have:

1. **Python 3.11+** - Required for the CLI
2. **uv** (recommended) or pip - For package management
3. **Claude Code CLI** - For plan generation and implementation
4. **Codex CLI** - For ChatGPT critique (plan/code gates)
5. **Linear API Key** - For fetching issues
6. **Git** - For branch/worktree isolation

### Step 1: Install Python Dependencies

Using uv (recommended):

```bash
cd ai-loop
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -e .
```

Using pip:

```bash
cd ai-loop
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

### Step 2: Install Claude Code CLI

Claude Code CLI is used for generating plans and implementing code.

```bash
# Install via npm (recommended)
npm install -g @anthropic-ai/claude-code

# Verify installation
claude --version

# Authenticate (one-time)
claude auth login
```

See [Claude Code documentation](https://docs.anthropic.com/claude-code) for more details.

### Step 3: Install Codex CLI

Codex CLI provides ChatGPT-powered critique for plan and code gates.

```bash
# Install via npm
npm install -g @openai/codex

# Authenticate with ChatGPT account (one-time, interactive)
codex login

# Verify installation
codex --version
```

**Important**: Codex uses ChatGPT account authentication (interactive browser login), NOT OpenAI API keys. The CLI will open a browser for you to sign in.

### Step 4: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# Required
LINEAR_API_KEY=lin_api_xxxxxxxxxxxxx

# Optional: CLI commands (defaults shown)
CLAUDE_CMD=claude
CODEX_CMD=codex

# Optional: Pipeline defaults
DRY_RUN_DEFAULT=true
MAX_ITERATIONS_DEFAULT=5
CONFIDENCE_THRESHOLD_DEFAULT=97
STABLE_PASSES_DEFAULT=2

# Optional: Feature flags
NO_LINEAR_WRITEBACK_DEFAULT=false
USE_WORKTREE_DEFAULT=true
```

### Step 5: Get a Linear API Key

1. Go to [Linear Settings → API](https://linear.app/settings/api)
2. Create a new Personal API Key
3. Copy the key (starts with `lin_api_`)
4. Add to your `.env` file

## Usage

### Web Dashboard

The web dashboard provides real-time monitoring of pipeline runs with an expandable timeline view.

```bash
# Development mode (auto-reload on code changes)
ai-loop dev --port 8080

# Production mode
ai-loop serve --port 8080

# Specify project directory
ai-loop serve --project /path/to/repo
```

The dashboard opens automatically in your browser at `http://localhost:8080`.

**Dashboard Features:**
- Real-time timeline of pipeline events
- Expandable plan/gate cards with markdown rendering
- Agent badges (Claude purple, ChatGPT green)
- Issue picker with team/state filtering
- Human-in-the-loop gate approval/rejection

### Single Issue (CLI)

```bash
# Dry run (no branches, no implementation)
ai-loop run --issue LIN-123 --dry-run

# Full run
ai-loop run --issue LIN-123 --no-dry-run

# With custom thresholds
ai-loop run --issue LIN-123 \
  --confidence-threshold 95 \
  --max-iterations 3 \
  --stable-passes 2
```

### Batch Processing

```bash
# Process all Todo issues in a team
ai-loop batch --team Engineering --state Todo --limit 10 --concurrency 3 --dry-run

# Filter by project and label
ai-loop batch --project "Q1 Roadmap" --label feature --limit 5

# Process specific issues
ai-loop batch --issues DIS-56,DIS-57,DIS-58

# Full batch run
ai-loop batch --team Engineering --limit 5 --concurrency 2 --no-dry-run
```

### View Runs

```bash
# List recent runs
ai-loop list-runs

# Watch a specific run's trace log
ai-loop watch --run-id lin-123-20240115-143022-abc123
```

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         PLANNING PHASE                          │
├─────────────────────────────────────────────────────────────────┤
│  1. Fetch Linear Issue                                          │
│  2. Claude generates initial plan                               │
│  3. ChatGPT PLAN_GATE critique                                  │
│  4. If not approved: Claude refines → goto 3                    │
│  5. Repeat until stable passes reached or max iterations        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       IMPLEMENTATION PHASE                       │
├─────────────────────────────────────────────────────────────────┤
│  6. Create branch/worktree                                      │
│  7. Claude implements approved plan                             │
│  8. ChatGPT CODE_GATE critique (reads diff, runs tests)         │
│  9. If blockers: Claude fixes → goto 8                          │
│  10. Max 3 fix attempts                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                          COMPLETION                              │
├─────────────────────────────────────────────────────────────────┤
│  11. Write summary to artifacts/<run_id>/                       │
│  12. Optionally comment on Linear issue                         │
│  13. Branch ready for review                                    │
└─────────────────────────────────────────────────────────────────┘
```

## Artifacts

Each run creates artifacts in `artifacts/<run_id>/`:

```
artifacts/lin-123-20240115-143022-abc123/
├── summary.json          # Run summary with status
├── trace.jsonl           # Append-only event log
├── issue_pack.md         # Sanitized issue content
├── plan_v1.md            # Initial plan
├── plan_v2.md            # Refined plan (if needed)
├── plan_gate_v1.json     # First critique
├── plan_gate_v2.json     # Second critique (if needed)
├── final_plan.md         # Approved plan
├── implement_log.txt     # Implementation output
├── code_gate_v1.json     # Code critique
└── worktree/             # Git worktree (if used)
```

## CLI Reference

```
ai-loop --help

Commands:
  run         Run pipeline for a single Linear issue
  batch       Process multiple issues with live dashboard
  serve       Start web dashboard (production)
  dev         Start web dashboard with auto-reload (development)
  watch       Tail a run's trace log
  list-runs   List recent runs
```

### Common Options

| Option | Description | Default |
|--------|-------------|---------|
| `--dry-run / --no-dry-run` | Skip branch creation and implementation | `true` |
| `--confidence-threshold` | Minimum critique confidence to pass | `97` |
| `--max-iterations` | Max planning refinement loops | `5` |
| `--stable-passes` | Consecutive passes needed | `2` |
| `--use-worktree / --no-use-worktree` | Use git worktree for isolation | `true` |

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run specific test
pytest tests/test_sanitizer.py -v

# Start dev server with auto-reload
ai-loop dev --port 8080
```

### Project Structure

```
ai-loop/
├── src/ai_loop/
│   ├── cli.py              # CLI commands (typer)
│   ├── dev_server.py       # Auto-reload dev server
│   ├── core/
│   │   ├── orchestrator.py # Pipeline orchestration
│   │   ├── models.py       # Pydantic models
│   │   └── dashboard.py    # Terminal UI
│   ├── integrations/
│   │   ├── linear_client.py    # Linear API
│   │   ├── claude_runner.py    # Claude Code CLI wrapper
│   │   └── openai_critique_runner.py  # Codex CLI wrapper
│   └── web/
│       ├── server.py       # HTTP server + SSE
│       ├── security.py     # Localhost security
│       └── static/v2/      # Dashboard frontend
├── artifacts/              # Run outputs (gitignored)
├── .env.example            # Environment template
└── pyproject.toml          # Package config
```

## Troubleshooting

### "claude: command not found"

Install Claude Code CLI:
```bash
npm install -g @anthropic-ai/claude-code
claude auth login
```

### "codex: command not found"

Install Codex CLI:
```bash
npm install -g @openai/codex
codex login  # Opens browser for ChatGPT auth
```

### "Invalid Linear API key"

1. Check that `LINEAR_API_KEY` is set in `.env`
2. Verify the key at [Linear Settings → API](https://linear.app/settings/api)
3. Ensure the key has read access to your workspace

### Web dashboard shows "Pairing required"

The dashboard uses localhost security. If you see this:
1. The pairing token is printed in the terminal when you start the server
2. Enter the token in the browser
3. Or restart with `ai-loop dev` which auto-opens the browser with the token

### Pipeline stuck at gate

If the pipeline is blocked at a gate:
1. Open the web dashboard
2. Review the critique feedback
3. Click "Approve" to proceed or "Reject" to fail the run
4. Optionally add feedback for Claude to incorporate

## Security

- **Localhost Only**: Server binds to `127.0.0.1`, never `0.0.0.0`
- **Pairing Token**: Required for all mutations (auto-handled by `ai-loop dev`)
- **CSRF Protection**: Synchronizer tokens prevent cross-site attacks
- **DNS Rebinding Defense**: Origin header validation
- **Input Sanitization**: Issue content sanitized to prevent injection
- **Secrets Scanning**: LLM outputs scanned and redacted before logging
- **Loop Guards**: Max iterations, stable passes, repetition detection

## License

MIT
