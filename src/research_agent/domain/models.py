from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class ResearchStage(StrEnum):
    CREATED = "CREATED"
    SEARCHED = "SEARCHED"
    SEARCH_REVIEW_PENDING = "SEARCH_REVIEW_PENDING"
    SCREENED = "SCREENED"
    EXTRACTED = "EXTRACTED"
    SYNTHESIZED = "SYNTHESIZED"
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEWED = "REVIEWED"
    OUTLINED = "OUTLINED"
    NARRATED = "NARRATED"
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
    candidates: list[PaperCandidate] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    screening_decisions: dict[str, str] = Field(default_factory=dict)
    screening_reasons: dict[str, str] = Field(default_factory=dict)
    coverage_gaps: list[str] = Field(default_factory=list)
    search_iteration_log: list[dict] = Field(default_factory=list)
    selection_notes: list[str] = Field(default_factory=list)


class ManualPaperInput(BaseModel):
    paper_id: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    url: HttpUrl | None = None
    source: str = "user"

    @model_validator(mode="after")
    def require_identifier(self) -> "ManualPaperInput":
        if self.doi.strip() or (self.paper_id.strip() and self.title.strip()):
            return self
        raise ValueError("Manual paper requires a DOI or both paper_id and title")


class SearchFeedback(BaseModel):
    action: Literal["refine", "accept", "stop"]
    suggested_queries: list[str] = Field(default_factory=list)
    added_papers: list[ManualPaperInput] = Field(default_factory=list)
    excluded_paper_ids: list[str] = Field(default_factory=list)
    comment: str = ""
    min_papers: int | None = Field(default=None, ge=1)
    max_papers: int | None = Field(default=None, ge=1)
    max_search_rounds: int | None = Field(default=None, ge=0)


class CandidateSetSnapshot(BaseModel):
    candidates: list[PaperCandidate]
    excluded_paper_ids: list[str] = Field(default_factory=list)
    executed_queries: list[str] = Field(default_factory=list)
    search_round: int = Field(default=0, ge=0)
    max_search_rounds: int = Field(default=3, ge=0)
    min_papers: int = Field(default=1, ge=1)
    max_papers: int = Field(default=8, ge=1)
    agent_included_paper_ids: list[str] = Field(default_factory=list)
    agent_excluded_paper_ids: list[str] = Field(default_factory=list)
    agent_uncertain_paper_ids: list[str] = Field(default_factory=list)
    agent_screening_reasons: dict[str, str] = Field(default_factory=dict)
    agent_approved: bool = False
    agent_review_note: str = ""
    user_comments: list[str] = Field(default_factory=list)
    search_failures: list[str] = Field(default_factory=list)


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


# ── DeepSynthesis: narrative review models ──────────────────────────


class SectionBrief(BaseModel):
    section_id: str
    heading: str
    assigned_paper_ids: list[str] = Field(default_factory=list)
    assigned_evidence_ids: list[str] = Field(default_factory=list)
    key_claims: list[str] = Field(default_factory=list)
    target_words: int = 300


class ReviewOutline(BaseModel):
    title: str
    narrative_arc: str
    sections: list[SectionBrief]
    writing_style: str = "academic-survey"


class SectionDraft(BaseModel):
    section_id: str
    heading: str
    content: str
    cited_evidence: list[str] = Field(default_factory=list)
    transition_from: str = ""
    transition_to: str = ""


class NarrativeSection(BaseModel):
    section_id: str
    heading: str
    content: str
    subsections: list[NarrativeSection] = Field(default_factory=list)
    cited_evidence: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    paper_id: str
    text: str
    bibtex: str = ""


class NarrativeReview(BaseModel):
    title: str
    abstract: str
    sections: list[NarrativeSection]
    references: list[Citation]
    writing_style: str = "academic-survey"
    word_count: int = 0
    evidence_chain: dict[str, list[str]] = Field(default_factory=dict)


class FactCheckIssue(BaseModel):
    claim: str
    evidence_id: str
    problem: str
    correction: str = ""


class FactCheckReport(BaseModel):
    section_id: str
    verdict: Literal["PASS", "REVISE"]
    issues: list[FactCheckIssue] = Field(default_factory=list)
