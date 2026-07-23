from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


DEFAULT_USER_ID = "local-user"


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


class SearchConstraints(BaseModel):
    year_from: int = Field(default=2024, ge=2000, le=2026)
    year_to: int = Field(default=2026, ge=2000, le=2026)
    prefer_library_search: bool = False

    @model_validator(mode="after")
    def validate_year_range(self) -> "SearchConstraints":
        if self.year_from > self.year_to:
            raise ValueError("year_from cannot be greater than year_to")
        return self


class PaperCandidate(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str | None = None
    url: HttpUrl | None = None
    source: str
    sources: list[str] = Field(default_factory=list)
    matched_queries: list[str] = Field(default_factory=list)
    relevance_score: float | None = None
    fields_of_study: list[str] = Field(default_factory=list)
    publication_type: str = ""
    citation_counts: dict[str, int] = Field(default_factory=dict)
    citation_percentiles: dict[str, float] = Field(default_factory=dict)
    fwci: float | None = None
    influential_citation_counts: dict[str, int] = Field(default_factory=dict)
    influential_citation_percentile: float | None = None
    recent_citation_velocities: dict[str, float] = Field(default_factory=dict)
    momentum_percentiles: dict[str, float] = Field(default_factory=dict)
    impact_score: float | None = None
    impact_confidence: float | None = None
    authority_score: float | None = None
    diversity_score: float | None = None
    composite_score: float | None = None
    is_retracted: bool = False
    impact_explanation: list[str] = Field(default_factory=list)
    authority_explanation: list[str] = Field(default_factory=list)
    ranking_explanation: list[str] = Field(default_factory=list)
    library_id: str = ""
    venue: str = ""
    venue_type: Literal["journal", "conference"] | None = None
    venue_acronym: str = ""
    ccf_rank: str | None = None
    ccf_category: str | None = None
    ccf_year: int | None = None
    sci_quartile: Literal["Q1", "Q2", "Q3", "Q4"] | None = None
    index_name: str | None = None
    impact_factor: float | None = None
    impact_factor_year: int | None = None
    nature_portfolio: bool = False
    venue_rating_explanation: str = ""
    venue_rating_source_url: str | None = None
    venue_rating_source_label: str | None = None
    venue_match_confidence: float | None = None
    agent_decision: str = ""
    agent_screening_reason: str = ""


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
    action: Literal["refine", "accept", "stop", "undo"]
    suggested_queries: list[str] = Field(default_factory=list)
    added_papers: list[ManualPaperInput] = Field(default_factory=list)
    excluded_paper_ids: list[str] = Field(default_factory=list)
    comment: str = ""
    min_papers: int | None = Field(default=None, ge=1)
    max_papers: int | None = Field(default=None, ge=1)


class CandidateSetSnapshot(BaseModel):
    candidates: list[PaperCandidate]
    selected_paper_ids: list[str] = Field(default_factory=list)
    filtered_candidates: list[PaperCandidate] = Field(default_factory=list)
    filtered_candidate_reasons: dict[str, list[str]] = Field(default_factory=dict)
    blocked_reason: str = ""
    excluded_paper_ids: list[str] = Field(default_factory=list)
    executed_queries: list[str] = Field(default_factory=list)
    query_rounds: list[list[str]] = Field(default_factory=list)
    search_round: int = Field(default=0, ge=0)
    max_search_rounds: int = Field(default=3, ge=1, le=10)
    manual_query_rounds: list[list[str]] = Field(default_factory=list)
    manual_search_round: int = Field(default=0, ge=0)
    max_manual_search_rounds: int = Field(default=3, ge=0)
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
    # Legacy candidate snapshots predate configurable year constraints. Keep
    # their range unknown instead of relabelling preserved results as 2024-2026.
    year_from: int | None = Field(default=None, ge=2000, le=2026)
    year_to: int | None = Field(default=None, ge=2000, le=2026)
    prefer_library_search: bool = False

    @model_validator(mode="after")
    def validate_year_range(self) -> "CandidateSetSnapshot":
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from cannot be greater than year_to")
        return self


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
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchGap(BaseModel):
    description: str
    supporting_paper_ids: list[str] = Field(default_factory=list)
    conflicting_paper_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
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
    name: str = ""
    topic: str
    research_question: str
    user_id: str = DEFAULT_USER_ID
    conversation_id: str = ""
    stage: ResearchStage = ResearchStage.CREATED
    current_review: ReviewResult | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserAccount(BaseModel):
    user_id: str
    display_name: str = "本地用户"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ResearchConversation(BaseModel):
    conversation_id: str
    user_id: str = DEFAULT_USER_ID
    project_id: str
    thread_id: str
    title: str
    research_question: str
    pinned: bool = False
    pinned_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationRun(BaseModel):
    run_id: str
    user_id: str = DEFAULT_USER_ID
    conversation_id: str
    project_id: str
    thread_id: str
    kind: Literal["initial", "continue"] = "initial"
    status: Literal[
        "queued",
        "running",
        "awaiting_input",
        "completed",
        "inconclusive",
        "failed",
        "interrupted",
        "cancelled",
    ] = "queued"
    phase: str = "thinking"
    message: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationMessage(BaseModel):
    message_id: int | None = None
    conversation_id: str
    user_id: str = DEFAULT_USER_ID
    role: Literal["user", "assistant", "system"]
    content: str
    run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


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


class LibraryPaper(BaseModel):
    """Canonical bibliographic record shared by every research project."""

    library_id: str
    paper_id: str = ""
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    url: HttpUrl | None = None
    source: str = "user"
    venue: str = ""
    venue_type: Literal["journal", "conference"] | None = None
    venue_acronym: str = ""
    ccf_rank: str | None = None
    ccf_category: str | None = None
    ccf_year: int | None = None
    sci_quartile: Literal["Q1", "Q2", "Q3", "Q4"] | None = None
    index_name: str | None = None
    impact_factor: float | None = None
    impact_factor_year: int | None = None
    nature_portfolio: bool = False
    venue_rating_explanation: str = ""
    venue_rating_source_url: str | None = None
    venue_rating_source_label: str | None = None
    tags: list[str] = Field(default_factory=list)
    starred: bool = False
    saved: bool = True
    archived_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LibraryCollection(BaseModel):
    """User-managed folder used to organize canonical library records."""

    collection_id: str
    name: str
    parent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LibraryNote(BaseModel):
    """Reusable reading note attached to one canonical paper."""

    note_id: str
    library_id: str
    content: str
    project_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ResearchNote(BaseModel):
    """Persistent note, review annotation, or saved Q&A for one research project."""

    note_id: str
    project_id: str
    kind: Literal["note", "annotation", "qa"] = "note"
    selected_text: str = ""
    content: str = ""
    question: str = ""
    answer: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PaperAnnotation(BaseModel):
    """Page-aware highlight, note, or saved Q&A in the paper workspace."""

    annotation_id: str
    library_id: str
    attachment_id: str | None = None
    kind: Literal["highlight", "note", "qa"]
    page: int | None = None
    selected_text: str = ""
    prefix: str = ""
    suffix: str = ""
    rects: list[dict[str, float]] = Field(default_factory=list)
    color: str = "yellow"
    content: str = ""
    question: str = ""
    answer: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PaperReadingProgress(BaseModel):
    """Per-user resume point for continuing a paper reading session."""

    library_id: str
    attachment_id: str | None = None
    project_id: str | None = None
    page: int = 1
    updated_at: datetime = Field(default_factory=utc_now)


class LibraryAttachment(BaseModel):
    """File or URL reference associated with a canonical paper."""

    attachment_id: str
    library_id: str
    name: str
    url: str
    media_type: str = "application/pdf"
    full_text_status: Literal[
        "unavailable",
        "linked",
        "uploaded",
        "extracting",
        "indexed",
        "failed",
        "ready",
    ] = "linked"
    page_count: int = 0
    chunk_count: int = 0
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LibraryChunk(BaseModel):
    """Page-aware text fragment extracted from one library attachment."""

    chunk_id: str
    library_id: str
    attachment_id: str
    page: int | None = None
    chunk_index: int = 0
    text: str
    content_hash: str
    created_at: datetime = Field(default_factory=utc_now)


class LibraryFinding(BaseModel):
    """A claim grounded in an exact quote from an indexed library source."""

    claim: str
    quote: str
    page: int | None = None
    section: str | None = None
    source_scope: Literal["full_text", "abstract"] = "full_text"


class LibraryPaperAnalysis(BaseModel):
    """Reusable AI reading result for a paper stored outside project history."""

    summary: str = ""
    methods: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    findings: list[LibraryFinding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    evidence_level: Literal["full_text", "abstract"] = "full_text"


class LibraryArtifact(BaseModel):
    """Versioned library-level output such as an AI paper analysis."""

    artifact_id: str
    library_id: str
    attachment_id: str | None = None
    kind: str
    payload: dict[str, Any]
    mode: Literal["agent", "extractive"] = "agent"
    created_at: datetime = Field(default_factory=utc_now)


class LibraryAgentResponse(BaseModel):
    """Structured final response emitted by the tool-using library Agent."""

    answer: str
    cited_source_ids: list[str] = Field(default_factory=list)
    used_library_ids: list[str] = Field(default_factory=list)
    coverage_note: str = ""


class PaperQuestionCitation(BaseModel):
    """One page-grounded quotation supporting a single-paper answer."""

    page: int = Field(ge=1)
    quote: str = Field(min_length=1)


class PaperQuestionAnswer(BaseModel):
    """Structured answer produced after reading a complete paper context."""

    answer: str
    citations: list[PaperQuestionCitation] = Field(default_factory=list)
    coverage_note: str = ""


class ProjectPaper(BaseModel):
    """Project-specific judgement for one globally shared paper."""

    project_id: str
    library_id: str
    source_paper_id: str = ""
    status: Literal["candidate", "included", "excluded", "uncertain"] = "candidate"
    reason: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ResearchRelation(BaseModel):
    """Directed inheritance link between two research projects.

    A child project inherits from a parent project, forming a research
    lineage DAG.  The ``note`` field carries user-authored rationale.
    """

    relation_id: str  # RR-<hex>
    parent_project_id: str
    child_project_id: str
    relation_type: str = "inherits"
    note: str = ""
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
