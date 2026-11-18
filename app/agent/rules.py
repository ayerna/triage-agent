"""
Deterministic rules for triage validation.
Runs regardless of what the model proposed.
Spec:
- If title/description mentions safety, fire, flooding, or injury, severity must be at least High.
- Severity and category must be from the allowed lists.
"""
from typing import get_args
from app.agent.schemas import AllowedCategory, AllowedSeverity, RuleCheckResult, TriageProposal

CRITICAL_KEYWORDS = {"safety", "fire", "flooding", "injury"}
ALLOWED_CATEGORIES = set(get_args(AllowedCategory))
ALLOWED_SEVERITIES = set(get_args(AllowedSeverity))

SEVERITY_LEVELS = {
    "Low": 1,
    "Medium": 2,
    "High": 3,
    "Critical": 4,
}


def deterministic_rule_check(
    title: str,
    description: str,
    proposal: TriageProposal,
) -> tuple[TriageProposal, RuleCheckResult]:
    """
    Apply deterministic safety rules to the proposal.
    Returns (adjusted_proposal, rule_check_result).
    """
    text_content = f"{title} {description}".lower()
    matched_keywords = [kw for kw in CRITICAL_KEYWORDS if kw in text_content]

    category_valid = proposal.category in ALLOWED_CATEGORIES
    severity_valid = proposal.severity in ALLOWED_SEVERITIES

    original_severity = proposal.severity
    applied_severity = original_severity
    forced_by_rule = False
    rule_reason = None

    # Rule: If critical keywords found, severity must be at least High (level >= 3)
    if matched_keywords:
        current_level = SEVERITY_LEVELS.get(original_severity, 1)
        if current_level < SEVERITY_LEVELS["High"]:
            applied_severity = "High"
            forced_by_rule = True
            rule_reason = (
                f"Critical safety keywords detected: {', '.join(matched_keywords)}. "
                f"Severity escalated from {original_severity} to High."
            )

    # Produce updated proposal if severity changed
    adjusted_proposal = proposal.model_copy(update={"severity": applied_severity})

    result = RuleCheckResult(
        passed=not forced_by_rule and category_valid and severity_valid,
        original_severity=original_severity,
        applied_severity=applied_severity,
        forced_by_rule=forced_by_rule,
        rule_reason=rule_reason,
        category_valid=category_valid,
        severity_valid=severity_valid,
    )

    return adjusted_proposal, result
