"""Data models for AI Loop pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    """Status of a pipeline run."""

    PENDING = "pending"
    PLANNING = "planning"
    PLAN_GATE = "plan_gate"
    REFINING = "refining"
    IMPLEMENTING = "implementing"
    CODE_GATE = "code_gate"
    FIXING = "fixing"
    SUCCESS = "success"
    FAILED = "failed"
    STUCK = "stuck"


class GateResult(str, Enum):
    """Result of a gate check."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


@dataclass
class LinearIssue:
    """Represents a Linear issue."""

    id: str
    identifier: str
    title: str
    description: str | None
    state: str
    priority: int
    team_id: str
    team_name: str
    project_id: str | None = None
    project_name: str | None = None
    labels: list[str] = field(default_factory=list)
    url: str = ""

    def to_issue_pack(self) -> str:
        """Convert to issue pack markdown format."""
        lines = [
            f"# Issue: {self.identifier}",
            "",
            f"**Title:** {self.title}",
            f"**Team:** {self.team_name}",
            f"**State:** {self.state}",
            f"**Priority:** {self.priority}",
        ]
        if self.project_name:
            lines.append(f"**Project:** {self.project_name}")
        if self.labels:
            lines.append(f"**Labels:** {', '.join(self.labels)}")
        if self.url:
            lines.append(f"**URL:** {self.url}")
        lines.extend(["", "## Description", "", self.description or "_No description_"])
        return "\n".join(lines)


class DiffInstruction(BaseModel):
    """Structured diff instruction from critique."""

    location: str = Field(description="File path and line/section")
    change_type: str = Field(description="add|remove|modify|move")
    before: str = Field(default="", description="Current state")
    after: str = Field(default="", description="Desired state")


class RubricBreakdown(BaseModel):
    """Rubric scores from critique (each 0-100)."""

    clarity_single_intent: int = Field(ge=0, le=100, description="Is outcome aligned with single intent?")
    smallest_vertical_slice: int = Field(ge=0, le=100, description="Did implementation stay minimal?")
    apple_native_ux: int = Field(ge=0, le=100, description="Are all user-visible states native?")
    single_source_of_truth: int = Field(ge=0, le=100, description="Single canonical source of truth?")
    simplicity_subtraction: int = Field(ge=0, le=100, description="Is code simpler than alternatives?")
    edge_cases_failure_modes: int = Field(ge=0, le=100, description="Are edge cases and failures handled?")
    testability_rollout_safety: int = Field(ge=0, le=100, description="Can this be rolled out safely?")
    consistency_with_patterns: int = Field(ge=0, le=100, description="Matches repo conventions?")


class CritiqueResult(BaseModel):
    """Structured output from Codex critique."""

    confidence: int = Field(ge=0, le=100, description="Confidence score 0-100")
    approved: bool = Field(description="Whether the artifact is approved")
    blockers: list[str] = Field(default_factory=list, description="Blocking issues")
    warnings: list[str] = Field(default_factory=list, description="Non-blocking warnings")
    feedback: str = Field(default="", description="Detailed feedback")
    diff_instructions: list[DiffInstruction] = Field(
        default_factory=list, description="Specific changes needed"
    )
    rubric_breakdown: RubricBreakdown = Field(
        default_factory=lambda: RubricBreakdown(
            clarity_single_intent=0,
            smallest_vertical_slice=0,
            apple_native_ux=0,
            single_source_of_truth=0,
            simplicity_subtraction=0,
            edge_cases_failure_modes=0,
            testability_rollout_safety=0,
            consistency_with_patterns=0,
        )
    )


@dataclass
class PlanVersion:
    """A version of the implementation plan."""

    version: int
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    hash: str = ""

    def __post_init__(self) -> None:
        import hashlib

        self.hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]


@dataclass
class RunContext:
    """Context for a pipeline run."""

    run_id: str
    issue: LinearIssue
    repo_root: Path
    artifacts_dir: Path
    worktree_dir: Path | None = None
    branch_name: str = ""
    dry_run: bool = True
    max_iterations: int = 5
    confidence_threshold: int = 97
    stable_passes: int = 2
    use_worktree: bool = True
    no_linear_writeback: bool = False
    verbose: bool = False

    # Runtime state
    status: RunStatus = RunStatus.PENDING
    current_iteration: int = 0
    stable_pass_count: int = 0
    plan_versions: list[PlanVersion] = field(default_factory=list)
    plan_gates: list[CritiqueResult] = field(default_factory=list)
    code_gates: list[CritiqueResult] = field(default_factory=list)
    final_plan: str = ""
    error_message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def working_dir(self) -> Path:
        """Get the working directory for implementation."""
        return self.worktree_dir if self.worktree_dir else self.repo_root


@dataclass
class TraceEvent:
    """Event for the trace log."""

    timestamp: datetime
    event_type: str
    stage: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "stage": self.stage,
            "data": self.data,
        }


@dataclass
class RunSummary:
    """Summary of a completed run."""

    run_id: str
    issue_identifier: str
    issue_title: str
    status: RunStatus
    iterations: int
    final_confidence: int | None
    branch_name: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "issue_identifier": self.issue_identifier,
            "issue_title": self.issue_title,
            "status": self.status.value,
            "iterations": self.iterations,
            "final_confidence": self.final_confidence,
            "branch_name": self.branch_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }
