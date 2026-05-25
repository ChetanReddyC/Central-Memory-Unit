from __future__ import annotations

from dataclasses import dataclass, field

from .retrieval import PreflightQuery


HIGH_RISK_TERMS = {
    "auth",
    "authentication",
    "billing",
    "credential",
    "credentials",
    "database",
    "deployment",
    "migration",
    "payment",
    "permission",
    "permissions",
    "privacy",
    "security",
}


@dataclass
class TriggerDecision:
    level: str
    reasons: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = ["CMU Trigger Decision", f"Level: {self.level}"]
        if self.reasons:
            lines.append("Reasons:")
            lines.extend(f"- {reason}" for reason in self.reasons)
        return "\n".join(lines)


def decide_trigger(
    query: PreflightQuery,
    *,
    repeated_error: bool = False,
    uncertainty: bool = False,
    shared_contract: bool = False,
    irreversible: bool = False,
    unfamiliar: bool = False,
) -> TriggerDecision:
    reasons = must_call_reasons(
        query,
        repeated_error=repeated_error,
        shared_contract=shared_contract,
        irreversible=irreversible,
    )
    if reasons:
        return TriggerDecision(level="must-call", reasons=reasons)
    reasons = should_call_reasons(query, uncertainty=uncertainty, unfamiliar=unfamiliar)
    if reasons:
        return TriggerDecision(level="should-call", reasons=reasons)
    return TriggerDecision(level="silent-skip", reasons=["small/local/low-risk with no trigger signals"])


def must_call_reasons(
    query: PreflightQuery,
    *,
    repeated_error: bool,
    shared_contract: bool,
    irreversible: bool,
) -> list[str]:
    reasons: list[str] = []
    text = trigger_text(query)
    matched_terms = sorted(term for term in HIGH_RISK_TERMS if term in text)
    if query.risk == "high":
        reasons.append("high risk task")
    if matched_terms:
        reasons.append(f"high-risk area: {', '.join(matched_terms[:3])}")
    if repeated_error:
        reasons.append("repeated error")
    if shared_contract:
        reasons.append("shared contract impact")
    if irreversible:
        reasons.append("hard-to-rollback change")
    return reasons


def should_call_reasons(query: PreflightQuery, *, uncertainty: bool, unfamiliar: bool) -> list[str]:
    reasons: list[str] = []
    if query.risk == "medium":
        reasons.append("medium risk task")
    if uncertainty:
        reasons.append("requirements or implementation uncertainty")
    if unfamiliar:
        reasons.append("unfamiliar module or workflow")
    if len(query.files or []) >= 3:
        reasons.append("multi-file task")
    return reasons


def trigger_text(query: PreflightQuery) -> str:
    return " ".join(
        [
            query.prompt,
            query.area,
            " ".join(query.files or []),
            " ".join(query.workflow or []),
            " ".join(query.environment or []),
        ]
    ).lower()
