from app.services.agentic import (
    ReviewDecision,
    ReviewFinding,
    ReviewFindingStatus,
    ReviewOutcome,
    ReviewSeverity,
    evaluate_review,
    normalize_review_outcome,
)


def finding(*, issue_key: str, finding_id: str | None = None, severity=ReviewSeverity.BLOCKING, status=ReviewFindingStatus.OPEN):
    return ReviewFinding(
        finding_id=finding_id,
        status=status,
        severity=severity,
        category="factual",
        issue_key=issue_key,
        locations=("slide:1",),
        description=issue_key,
        correction="correct it",
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
