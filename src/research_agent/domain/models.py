from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


def utc_now() -> datetime:
    return datetime.now(UTC)


class ResearchStage(StrEnum):
    CREATED = "CREATED"
    SEARCHED = "SEARCHED"
    SCREENED = "SCREENED"
    EXTRACTED = "EXTRACTED"
    SYNTHESIZED = "SYNTHESIZED"
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEWED = "REVIEWED"
    COMPLETED = "COMPLETED"
    INCONCLUSIVE = "INCONCLUSIVE"


class ReviewVerdict(StrEnum):
    PASS = "PASS"
    REVISE = "REVISE"


class EvidenceConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PaperCandidate(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str | None = None
    url: HttpUrl | None = None
    source: str


class SearchReport(BaseModel):
    query: str
    search_terms: list[str]
    candidates: list[PaperCandidate]
    selection_notes: list[str] = Field(default_factory=list)


class ScreeningDecision(BaseModel):
    included_paper_ids: list[str]
    excluded_paper_ids: list[str] = Field(default_factory=list)
    reasons: list[str]


class InsufficientEvidence(BaseModel):
    reason: str
    queries_attempted: list[str] = Field(default_factory=list)
    search_failures: list[str] = Field(default_factory=list)
    recommendation: str


class Evidence(BaseModel):
    evidence_id: str
    paper_id: str
    claim: str
    quote: str
    page: int | None = None
    section: str | None = None


class PaperCard(BaseModel):
    paper_id: str
    title: str
    research_question: str
    methods: list[str]
    datasets: list[str] = Field(default_factory=list)
    findings: list[Evidence]
    limitations: list[str] = Field(default_factory=list)


class EvidenceBackedClaim(BaseModel):
    statement: str
    evidence_ids: list[str] = Field(min_length=1)


class ResearchGap(BaseModel):
    description: str
    supporting_paper_ids: list[str] = Field(min_length=1)
    conflicting_paper_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: EvidenceConfidence
    proposed_hypothesis: str


class SynthesisReport(BaseModel):
    topic: str
    consensus: list[EvidenceBackedClaim]
    conflicts: list[EvidenceBackedClaim]
    method_comparison: list[EvidenceBackedClaim]
    gaps: list[ResearchGap]


class ReviewResult(BaseModel):
    verdict: ReviewVerdict
    fatal_issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)


class ResearchProject(BaseModel):
    project_id: str
    topic: str
    research_question: str
    stage: ResearchStage = ResearchStage.CREATED
    current_review: ReviewResult | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class StateEvent(BaseModel):
    event_id: int | None = None
    project_id: str
    from_stage: ResearchStage
    to_stage: ResearchStage
    actor: str
    created_at: datetime = Field(default_factory=utc_now)
    artifact_hash: str
    review_verdict: ReviewVerdict | None = None


class ArtifactRecord(BaseModel):
    artifact_id: int | None = None
    project_id: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)
