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
                clarity_single_intent=95,
                smallest_vertical_slice=90,
                apple_native_ux=95,
                single_source_of_truth=92,
                simplicity_subtraction=90,
                edge_cases_failure_modes=85,
                testability_rollout_safety=95,
                consistency_with_patterns=90,
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
                clarity_single_intent=95,
                smallest_vertical_slice=90,
                apple_native_ux=95,
                single_source_of_truth=92,
                simplicity_subtraction=90,
                edge_cases_failure_modes=85,
                testability_rollout_safety=95,
                consistency_with_patterns=90,
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
                clarity_single_intent=85,
                smallest_vertical_slice=85,
                apple_native_ux=85,
                single_source_of_truth=85,
                simplicity_subtraction=85,
                edge_cases_failure_modes=85,
                testability_rollout_safety=85,
                consistency_with_patterns=85,
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
                clarity_single_intent=95,
                smallest_vertical_slice=90,
                apple_native_ux=80,
                single_source_of_truth=92,
                simplicity_subtraction=90,
                edge_cases_failure_modes=85,
                testability_rollout_safety=95,
                consistency_with_patterns=90,
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
                clarity_single_intent=97,
                smallest_vertical_slice=97,
                apple_native_ux=97,
                single_source_of_truth=97,
                simplicity_subtraction=97,
                edge_cases_failure_modes=97,
                testability_rollout_safety=97,
                consistency_with_patterns=97,
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
                clarity_single_intent=96,
                smallest_vertical_slice=96,
                apple_native_ux=96,
                single_source_of_truth=96,
                simplicity_subtraction=96,
                edge_cases_failure_modes=96,
                testability_rollout_safety=96,
                consistency_with_patterns=96,
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
                clarity_single_intent=101,  # Invalid
                smallest_vertical_slice=90,
                apple_native_ux=90,
                single_source_of_truth=90,
                simplicity_subtraction=90,
                edge_cases_failure_modes=90,
                testability_rollout_safety=90,
                consistency_with_patterns=90,
            )

    def test_confidence_validation(self):
        with pytest.raises(ValueError):
            CritiqueResult(
                confidence=150,  # Invalid
                approved=True,
                feedback="Test",
            )
