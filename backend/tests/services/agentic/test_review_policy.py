import pytest

from app.services.agentic import (
    ReviewDecision,
    ReviewFinding,
    ReviewFindingStatus,
    ReviewOutcome,
    ReviewSeverity,
    evaluate_review,
    normalize_review_outcome,
)


def finding(
    *,
    issue_key: str,
    finding_id: str | None = None,
    severity=ReviewSeverity.BLOCKING,
    status=ReviewFindingStatus.OPEN,
    category="factual",
    locations=("slide:1",),
    description: str | None = None,
    correction="correct it",
):
    return ReviewFinding(
        finding_id=finding_id,
        status=status,
        severity=severity,
        category=category,
        issue_key=issue_key,
        locations=locations,
        description=description or issue_key,
        correction=correction,
    )


def review(*findings_: ReviewFinding) -> ReviewOutcome:
    return ReviewOutcome(summary="review", findings=list(findings_))


def evaluate(current, previous=None, *, attempt=1, stagnant=0, max_attempts=5, limit=2):
    normalized = normalize_review_outcome(current, previous, attempt=attempt)
    return normalized, evaluate_review(
        normalized,
        previous,
        attempt=attempt,
        max_attempts=max_attempts,
        stagnant_transitions=stagnant,
        stagnation_limit=limit,
    )


def test_converging_blockers_continue_until_clean():
    first, first_eval = evaluate(review(finding(issue_key="a"), finding(issue_key="b")), attempt=1)
    assert first_eval.decision is ReviewDecision.RETRY
    second, second_eval = evaluate(
        review(
            finding(issue_key="a", finding_id=first.findings[0].finding_id),
            finding(issue_key="b", finding_id=first.findings[1].finding_id),
        ),
        first,
        attempt=2,
    )
    assert second_eval.persistent_count == 2
    assert second_eval.stagnant_transitions == 1
    assert second_eval.decision is ReviewDecision.RETRY
    _, clean_eval = evaluate(
        review(
            finding(issue_key="a", finding_id=second.findings[0].finding_id, status=ReviewFindingStatus.RESOLVED),
            finding(issue_key="b", finding_id=second.findings[1].finding_id, status=ReviewFindingStatus.RESOLVED),
        ),
        second,
        attempt=3,
    )
    assert clean_eval.decision is ReviewDecision.PUBLISH


def test_two_no_progress_transitions_reject_as_stagnated():
    first, _ = evaluate(review(finding(issue_key="a")), attempt=1)
    second, second_eval = evaluate(review(finding(issue_key="a", finding_id=first.findings[0].finding_id)), first, attempt=2)
    assert second_eval.decision is ReviewDecision.RETRY
    _, third_eval = evaluate(
        review(finding(issue_key="a", finding_id=second.findings[0].finding_id)),
        second,
        attempt=3,
        stagnant=second_eval.stagnant_transitions,
    )
    assert third_eval.decision is ReviewDecision.REJECT_STAGNATED
    assert third_eval.stagnant_transitions == 2


def test_new_blocker_churn_does_not_hide_resolution_progress():
    first, _ = evaluate(review(finding(issue_key="a")), attempt=1)
    second, second_eval = evaluate(
        review(finding(issue_key="b"), finding(issue_key="a", finding_id=first.findings[0].finding_id, status=ReviewFindingStatus.RESOLVED)),
        first,
        attempt=2,
    )
    assert second_eval.resolved_count == 1
    assert second_eval.new_count == 1
    assert second_eval.stagnant_transitions == 0
    assert second_eval.decision is ReviewDecision.RETRY
    assert second.findings[0].finding_id


def test_advisories_do_not_block_publication():
    _, result = evaluate(review(finding(issue_key="polish", severity=ReviewSeverity.ADVISORY)), attempt=1)
    assert result.decision is ReviewDecision.PUBLISH
    assert result.blocking_count == 0
    assert result.advisory_count == 1


def test_max_attempts_wins_when_blocker_remains():
    first, _ = evaluate(review(finding(issue_key="a")), attempt=1)
    _, result = evaluate(
        review(finding(issue_key="a", finding_id=first.findings[0].finding_id)),
        first,
        attempt=5,
    )
    assert result.decision is ReviewDecision.REJECT_MAX_ATTEMPTS


def test_normalization_rejects_omitted_previous_blocker():
    first, _ = evaluate(review(finding(issue_key="a")), attempt=1)
    try:
        normalize_review_outcome(review(), first, attempt=2)
    except ValueError as exc:
        assert "omitted" in str(exc)
    else:
        raise AssertionError("expected omitted blocker to be rejected")


def test_normalization_allows_location_and_prose_updates_for_existing_finding():
    first, _ = evaluate(review(finding(issue_key="unsupported-claim")), attempt=1)
    updated = normalize_review_outcome(
        review(
            finding(
                issue_key="unsupported-claim",
                finding_id=first.findings[0].finding_id,
                locations=("slide:4", "claim:C4"),
                description="The revised claim still lacks a mapping.",
                correction="Map the revised claim to evidence.",
            )
        ),
        first,
        attempt=2,
    )
    assert updated.findings[0].locations == ("slide:4", "claim:C4")
    assert updated.findings[0].description == "The revised claim still lacks a mapping."


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"issue_key": "different-issue"}, "identity"),
        ({"category": "visual"}, "identity"),
        ({"severity": ReviewSeverity.ADVISORY}, "severity"),
    ],
)
def test_normalization_rejects_mutating_existing_finding_identity_or_severity(change, error):
    first, _ = evaluate(review(finding(issue_key="unsupported-claim")), attempt=1)
    with pytest.raises(ValueError, match=error):
        normalize_review_outcome(
            review(finding(issue_key=change.pop("issue_key", "unsupported-claim"), finding_id=first.findings[0].finding_id, **change)),
            first,
            attempt=2,
        )


def test_normalization_rejects_severity_downgrade_when_reviewer_omits_an_existing_id():
    first, _ = evaluate(review(finding(issue_key="unsupported-claim")), attempt=1)
    with pytest.raises(ValueError, match="severity"):
        normalize_review_outcome(
            review(finding(issue_key="unsupported-claim", severity=ReviewSeverity.ADVISORY)),
            first,
            attempt=2,
        )


def test_real_progress_with_new_blockers_retries_without_stagnation():
    first, _ = evaluate(
        review(*(finding(issue_key=f"old-{index}") for index in range(4))),
        attempt=1,
    )
    second, result = evaluate(
        review(
            *(finding(issue_key=f"old-{index}", finding_id=first.findings[index].finding_id, status=ReviewFindingStatus.RESOLVED) for index in range(3)),
            finding(issue_key="old-3", finding_id=first.findings[3].finding_id),
            finding(issue_key="new-1"),
            finding(issue_key="new-2"),
        ),
        first,
        attempt=2,
    )
    assert len(second.findings) == 6
    assert result.decision is ReviewDecision.RETRY
    assert result.resolved_count == 3
    assert result.persistent_count == 1
    assert result.new_count == 2
    assert result.blocking_count == 3
    assert result.stagnant_transitions == 0
