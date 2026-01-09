# AI Loop

Visual dashboard for automating Linear issue implementation with AI-powered planning and critique gates.

AI Loop connects your Linear issues to Claude for planning and implementation, with OpenAI providing quality gates that critique plans and code before they ship. The primary interface is the **V2 web dashboard** - a real-time UI for managing runs, reviewing critiques, and providing feedback at gates.

## Features

### Visual Dashboard (Primary Interface)
- **Real-time run monitoring** - Live updates via Server-Sent Events
- **Issue picker** - Filter and select Linear issues by team, project, or state
- **Tabbed detail view** - Output, Files, and Critique tabs
- **Gate feedback** - Approve, reject, or request changes with inline feedback
- **Project switching** - Manage multiple repositories from one dashboard
- **Connection status** - Visual indicators for server connectivity

### Pipeline
- **Linear integration** - Fetch issues directly from your workspace
- **Claude planning** - AI-generated implementation plans
- **OpenAI critique gates** - PLAN_GATE and CODE_GATE with rubric scoring
- **Git isolation** - Branches or worktrees for safe experimentation
- **Artifact logging** - Full traceability with trace files and summaries

## Requirements

- Python 3.11+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) (authenticated)
- Linear API key
- OpenAI API key (for critique gates)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
cd ai-loop
uv venv && source .venv/bin/activate
uv pip install -e .
```

## Updating After Changes

The `-e` (editable) install means most code changes take effect immediately.

**Re-run install when:**
- Adding new dependencies to `pyproject.toml`
- Changing entry points or package structure
- Pulling changes that modify dependencies

```bash
cd ai-loop && uv sync
```

**Changes that take effect immediately:**
- Python source files in `src/ai_loop/`
- Prompt templates in `prompts/`
- Static assets in `web/static/`

## Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Add your API keys:
   ```bash
   # Required
   LINEAR_API_KEY=lin_api_xxxxxxxxxxxxx

   # Required for critique gates
   OPENAI_API_KEY=sk-xxxxxxxxxxxxx
   ```

3. Optional settings (defaults shown):
   ```bash
   CLAUDE_CMD=claude
   DRY_RUN_DEFAULT=true
   MAX_ITERATIONS_DEFAULT=5
   CONFIDENCE_THRESHOLD_DEFAULT=97
   STABLE_PASSES_DEFAULT=2
   USE_WORKTREE_DEFAULT=true
   ```

## Quick Start

### Visual Dashboard (Recommended)

```bash
# Start the dashboard
ai-loop serve --port 8080 --open

# Enable real implementations (not just dry-run)
ai-loop serve --port 8080 --open --enable-writes
```

Then:
1. Select issues from the left panel
2. Click "Start" to begin runs
3. Monitor progress in real-time
4. Review critiques and provide feedback at gates

### CLI (Alternative)

```bash
# Single issue (dry-run)
ai-loop run --issue LIN-123 --dry-run

# Single issue (full implementation)
ai-loop run --issue LIN-123 --no-dry-run

# Batch processing
ai-loop batch --team Engineering --state Todo --limit 10
```

## Visual Dashboard Features

| Area | Features |
|------|----------|
| **Header** | Project picker, approval mode selector (auto/gate-on-fail/always-gate), connection status |
| **Left Panel** | Issue picker with filters, run list grouped by status (active/pending/history) |
| **Right Panel** | Tabbed view: Output stream, File changes, Critique results |
| **Feedback Bar** | Appears at gates - approve, reject, or request changes with feedback text |

## CLI Reference

### `ai-loop serve` (Primary)

Start the visual dashboard:

```bash
ai-loop serve [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port` | 8080 | Server port |
| `--project` | auto-detect | Project directory |
| `--open` | false | Open browser automatically |
| `--enable-writes` | false | Allow real implementations |

### `ai-loop run`

Process a single issue:

```bash
ai-loop run --issue LIN-123 [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--dry-run/--no-dry-run` | true | Safe mode (no branches) |
| `--max-iterations` | 5 | Max plan refinement loops |
| `--confidence-threshold` | 97 | Gate approval threshold (0-100) |
| `--stable-passes` | 2 | Required consecutive passes |
| `--use-worktree/--no-worktree` | true | Git worktree isolation |
| `--no-linear-writeback` | false | Skip commenting on Linear |

### `ai-loop batch`

Process multiple issues:

```bash
ai-loop batch [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--issues` | - | Comma-separated issue IDs |
| `--team` | - | Filter by team name |
| `--project` | - | Filter by project name |
| `--state` | Todo | Filter by state |
| `--label` | - | Filter by label |
| `--limit` | 20 | Max issues to process |
| `--concurrency` | 5 | Parallel runs |

### `ai-loop list-runs`

View recent run history with status, iterations, and confidence scores.

### `ai-loop watch`

Tail a specific run's trace log:

```bash
ai-loop watch --run-id <run_id>
```

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         PLANNING PHASE                          │
├─────────────────────────────────────────────────────────────────┤
│  Linear Issue  ──►  Claude Plan  ──►  OpenAI PLAN_GATE          │
│                           ▲                  │                  │
│                           └──── Refine ◄─────┘                  │
│                              (if rejected)                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (plan approved)
┌─────────────────────────────────────────────────────────────────┐
│                      IMPLEMENTATION PHASE                        │
├─────────────────────────────────────────────────────────────────┤
│  Create Branch  ──►  Claude Implement  ──►  OpenAI CODE_GATE    │
│                              ▲                    │             │
│                              └──── Fix ◄──────────┘             │
│                              (up to 3 attempts)                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (code approved)
┌─────────────────────────────────────────────────────────────────┐
│                          COMPLETION                              │
├─────────────────────────────────────────────────────────────────┤
│  Save Artifacts  ──►  Comment on Linear  ──►  Branch Ready      │
└─────────────────────────────────────────────────────────────────┘
```

### Stage Details

1. **PLANNING** - Claude generates an implementation plan from the Linear issue
2. **PLAN_GATE** - OpenAI critiques the plan using an 8-point rubric (clarity, simplicity, edge cases, etc.)
3. **REFINEMENT** - If rejected, Claude refines based on feedback (loops up to `max_iterations`)
4. **IMPLEMENTATION** - Claude implements the approved plan in an isolated git branch/worktree
5. **CODE_GATE** - OpenAI validates the implementation against the plan and test results
6. **FIXING** - If blockers found, Claude fixes (up to 3 attempts)
7. **COMPLETION** - Artifacts saved, optional Linear comment, branch ready for review

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LINEAR_API_KEY` | Yes | - | Linear API key |
| `OPENAI_API_KEY` | Yes* | - | OpenAI API key (*for critique gates) |
| `CLAUDE_CMD` | No | `claude` | Claude CLI command |
| `DRY_RUN_DEFAULT` | No | `true` | Default to dry-run mode |
| `MAX_ITERATIONS_DEFAULT` | No | `5` | Max plan iterations |
| `CONFIDENCE_THRESHOLD_DEFAULT` | No | `97` | Gate pass threshold (0-100) |
| `STABLE_PASSES_DEFAULT` | No | `2` | Required stable passes |
| `USE_WORKTREE_DEFAULT` | No | `true` | Use git worktree isolation |
| `NO_LINEAR_WRITEBACK_DEFAULT` | No | `false` | Skip Linear comments |
| `HTTP_TIMEOUT_SECS` | No | `60` | HTTP request timeout |

## Artifacts

Each run creates an artifacts directory:

```
artifacts/<run_id>/
├── summary.json          # Run metadata and final status
├── trace.jsonl           # Append-only event log
├── issue_pack.md         # Sanitized issue content
├── plan_v1.md            # Initial plan
├── plan_v2.md            # Refined plan (if iterations)
├── final_plan.md         # Approved plan
├── plan_gate_v1.json     # PLAN_GATE critique result
├── code_gate_v1.json     # CODE_GATE critique result
├── implement_log.txt     # Claude implementation output
└── worktree/             # Git worktree (if used)
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov
```

## Project Structure

```
ai-loop/
├── src/ai_loop/
│   ├── cli.py              # CLI entry points
│   ├── config.py           # Settings management
│   ├── core/               # Orchestrator, models, artifacts
│   ├── integrations/       # Linear, Claude, OpenAI, Git
│   ├── safety/             # Sanitization, secrets scanning
│   └── web/                # Dashboard server and static assets
├── prompts/                # AI prompt templates
├── schemas/                # JSON schemas for critique output
├── tests/                  # Test suite
└── pyproject.toml          # Package configuration
```
