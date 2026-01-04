# AI Loop

CLI orchestrator for automated Linear issue implementation with AI-powered planning and critique.

```
Linear Issues → Claude Plans → Codex Critique → Claude Implementation
```

## Features

- **Linear Integration**: Fetch issues by identifier or query by team/project/state/label
- **Two-Stage Gating**: PLAN_GATE and CODE_GATE ensure quality at each phase
- **Claude Builder**: Generate, refine, and implement plans using Claude Code CLI
- **Codex Critic**: Rigorous critique with structured JSON output
- **Git Isolation**: Work in branches or worktrees to avoid conflicts
- **Live Dashboard**: Rich terminal UI for batch runs with progress tracking
- **Artifact Logging**: Full trace of each run for debugging and auditing

## Installation

### Using uv (recommended)

```bash
cd ai-loop
uv venv
source .venv/bin/activate
uv pip install -e .
```

### Using pip

```bash
cd ai-loop
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Required:
- `LINEAR_API_KEY`: Your Linear API key

Optional:
- `CLAUDE_CMD`: Claude CLI command (default: `claude`)
- `CODEX_CMD`: Codex CLI command (default: `codex`)
- `DRY_RUN_DEFAULT`: Default dry-run mode (default: `true`)
- See `.env.example` for all options

## Prerequisites

### Claude CLI

Ensure Claude Code CLI is installed and authenticated:

```bash
claude --version
```

### Codex CLI

Codex CLI should be installed and authenticated via ChatGPT:

```bash
# Authenticate with ChatGPT account (one-time)
codex login

# Verify
codex --version
```

Note: Codex uses ChatGPT account authentication, NOT OpenAI API keys.

## Usage

### Single Issue

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

# Full batch run
ai-loop batch --team Engineering --limit 5 --concurrency 2 --no-dry-run
```

### View Runs

```bash
# List recent runs
ai-loop list-runs

# Watch a specific run
ai-loop watch --run-id lin-123-20240115-143022-abc123
```

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                         PLANNING PHASE                          │
├─────────────────────────────────────────────────────────────────┤
│  1. Fetch Linear Issue                                          │
│  2. Claude generates initial plan                               │
│  3. Codex PLAN_GATE critique                                    │
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
│  8. Codex CODE_GATE critique (reads diff, runs tests)           │
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
├── plan_v2.md            # Refined plan
├── plan_gate_v1.json     # First critique
├── plan_gate_v2.json     # Second critique
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
  watch       Tail a run's trace log
  list-runs   List recent runs
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run specific test
pytest tests/test_sanitizer.py -v
```

## Safety Features

- **Input Sanitization**: Issue content is sanitized to prevent injection
- **Secrets Scanning**: LLM outputs are scanned and redacted before logging
- **Loop Guards**: Max iterations, stable passes, repetition detection
- **Codex Sandboxing**: PLAN_GATE and CODE_GATE run in read-only mode

## License

MIT
