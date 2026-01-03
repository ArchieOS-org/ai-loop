"""Tests for gating logic."""

import pytest

from ai_loop.core.models import CritiqueResult, GateResult, RubricBreakdown


def check_gate(critique: CritiqueResult, threshold: int) -> GateResult:
    """Check if a gate passes."""
    if critique.approved and critique.confidence >= threshold and not critique.blockers:
        return GateResult.PASS
    return GateResult.FAIL


class TestGateLogic:
    """Tests for gate checking logic."""

    def test_passes_when_all_conditions_met(self):
        critique = CritiqueResult(
            confidence=98,
            approved=True,
            blockers=[],
            warnings=["Minor suggestion"],
            feedback="Looks good",
            diff_instructions=[],
            rubric_breakdown=RubricBreakdown(
                goal_clarity=95,
                scope_minimality=90,
                ux_contract=95,
                data_contract=92,
                architecture=90,
                test_coverage=85,
                rollout_safety=95,
                done_checklist=90,
            ),
        )
        assert check_gate(critique, threshold=97) == GateResult.PASS

    def test_fails_when_not_approved(self):
        critique = CritiqueResult(
            confidence=98,
            approved=False,
            blockers=[],
            warnings=[],
            feedback="Not approved",
            diff_instructions=[],
            rubric_breakdown=RubricBreakdown(
                goal_clarity=95,
                scope_minimality=90,
                ux_contract=95,
                data_contract=92,
                architecture=90,
                test_coverage=85,
                rollout_safety=95,
                done_checklist=90,
            ),
        )
        assert check_gate(critique, threshold=97) == GateResult.FAIL

    def test_fails_when_below_threshold(self):
        critique = CritiqueResult(
            confidence=85,
            approved=True,
            blockers=[],
            warnings=[],
            feedback="Good but not great",
            diff_instructions=[],
            rubric_breakdown=RubricBreakdown(
                goal_clarity=85,
                scope_minimality=85,
                ux_contract=85,
                data_contract=85,
                architecture=85,
                test_coverage=85,
                rollout_safety=85,
                done_checklist=85,
            ),
        )
        assert check_gate(critique, threshold=97) == GateResult.FAIL

    def test_fails_when_has_blockers(self):
        critique = CritiqueResult(
            confidence=98,
            approved=True,
            blockers=["Missing error handling"],
            warnings=[],
            feedback="Almost there",
            diff_instructions=[],
            rubric_breakdown=RubricBreakdown(
                goal_clarity=95,
                scope_minimality=90,
                ux_contract=80,
                data_contract=92,
                architecture=90,
                test_coverage=85,
                rollout_safety=95,
                done_checklist=90,
            ),
        )
        assert check_gate(critique, threshold=97) == GateResult.FAIL

    def test_passes_at_exact_threshold(self):
        critique = CritiqueResult(
            confidence=97,
            approved=True,
            blockers=[],
            warnings=["Minor"],
            feedback="Just meets threshold",
            diff_instructions=[],
            rubric_breakdown=RubricBreakdown(
                goal_clarity=97,
                scope_minimality=97,
                ux_contract=97,
                data_contract=97,
                architecture=97,
                test_coverage=97,
                rollout_safety=97,
                done_checklist=97,
            ),
        )
        assert check_gate(critique, threshold=97) == GateResult.PASS

    def test_fails_just_below_threshold(self):
        critique = CritiqueResult(
            confidence=96,
            approved=True,
            blockers=[],
            warnings=[],
            feedback="Just below threshold",
            diff_instructions=[],
            rubric_breakdown=RubricBreakdown(
                goal_clarity=96,
                scope_minimality=96,
                ux_contract=96,
                data_contract=96,
                architecture=96,
                test_coverage=96,
                rollout_safety=96,
                done_checklist=96,
            ),
        )
        assert check_gate(critique, threshold=97) == GateResult.FAIL


class TestCritiqueModel:
    """Tests for CritiqueResult model."""

    def test_default_values(self):
        critique = CritiqueResult(
            confidence=50,
            approved=False,
            feedback="Test",
        )
        assert critique.blockers == []
        assert critique.warnings == []
        assert critique.diff_instructions == []

    def test_rubric_validation(self):
        # Values must be 0-100
        with pytest.raises(ValueError):
            RubricBreakdown(
                goal_clarity=101,  # Invalid
                scope_minimality=90,
                ux_contract=90,
                data_contract=90,
                architecture=90,
                test_coverage=90,
                rollout_safety=90,
                done_checklist=90,
            )

    def test_confidence_validation(self):
        with pytest.raises(ValueError):
            CritiqueResult(
                confidence=150,  # Invalid
                approved=True,
                feedback="Test",
            )
