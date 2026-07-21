from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from research_agent.domain.models import SearchConstraints, SearchFeedback


class ResearchRequest(SearchConstraints):
    topic: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    thread_id: str | None = None
    min_papers: int | None = Field(default=None, ge=1)
    max_papers: int | None = Field(default=None, ge=1)
    max_search_rounds: int | None = Field(default=None, ge=0)
    prefer_library: bool = False


class CreateConversationRequest(SearchConstraints):
    topic: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    min_papers: int | None = Field(default=None, ge=1)
    max_papers: int | None = Field(default=None, ge=1)
    max_search_rounds: int | None = Field(default=None, ge=0)
    prefer_library: bool = False


class ApiEnvelope(BaseModel):
    code: int = 0
    message: str = "success"
    data: Any = None


class SearchFeedbackRequest(SearchFeedback):
    pass


class ContinueProjectRequest(BaseModel):
    thread_id: str | None = None


class LibraryPaperRequest(BaseModel):
    paper_id: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str = ""
    url: str | None = None
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


class ProjectLibraryPaperRequest(LibraryPaperRequest):
    status: Literal["candidate", "included", "excluded", "uncertain"] = "candidate"
    reason: str = ""


class LibraryPaperUpdateRequest(BaseModel):
    paper_id: str | None = None
    title: str | None = None
    authors: list[str] | None = None
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    url: str | None = None
    source: str | None = None
    venue: str | None = None
    venue_type: Literal["journal", "conference"] | None = None
    venue_acronym: str | None = None
    ccf_rank: str | None = None
    ccf_category: str | None = None
    ccf_year: int | None = None
    sci_quartile: Literal["Q1", "Q2", "Q3", "Q4"] | None = None
    index_name: str | None = None
    impact_factor: float | None = None
    impact_factor_year: int | None = None
    nature_portfolio: bool | None = None
    venue_rating_explanation: str | None = None
    venue_rating_source_url: str | None = None
    venue_rating_source_label: str | None = None
    tags: list[str] | None = None
    starred: bool | None = None
    saved: bool | None = None


class LibraryImportRequest(BaseModel):
    format: Literal["bibtex", "ris"]
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class LibraryCollectionRequest(BaseModel):
    name: str = Field(min_length=1)
    parent_id: str | None = None


class LibraryBulkRequest(BaseModel):
    library_ids: list[str] = Field(min_length=1)
    action: Literal[
        "archive",
        "restore",
        "delete",
        "star",
        "unstar",
        "add_tags",
        "remove_tags",
        "add_collection",
        "remove_collection",
        "add_project",
    ]
    value: Any = None


class LibraryNoteRequest(BaseModel):
    content: str = Field(min_length=1)
    project_id: str | None = None


class LibraryAttachmentRequest(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    media_type: str = "application/pdf"


class LibraryMergeRequest(BaseModel):
    primary_id: str
    duplicate_id: str


class LibrarySelectionRequest(BaseModel):
    library_ids: list[str] = Field(min_length=2, max_length=8)


class LibraryAssistantRequest(BaseModel):
    library_ids: list[str] = Field(default_factory=list, max_length=50)
    question: str = Field(min_length=1)


class PaperQuestionRequest(BaseModel):
    scope: Literal["selection", "paper"] = "paper"
    attachment_id: str | None = None
    question: str = Field(min_length=1, max_length=4000)
    page: int | None = Field(default=None, ge=1)
    selected_text: str = Field(default="", max_length=12000)
    prefix: str = Field(default="", max_length=1000)
    suffix: str = Field(default="", max_length=1000)


class PaperAnnotationRequest(BaseModel):
    kind: Literal["highlight", "note", "qa"]
    attachment_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    selected_text: str = Field(default="", max_length=12000)
    prefix: str = Field(default="", max_length=1000)
    suffix: str = Field(default="", max_length=1000)
    rects: list[dict[str, float]] = Field(default_factory=list, max_length=100)
    color: str = Field(default="yellow", max_length=32)
    content: str = Field(default="", max_length=20000)
    question: str = Field(default="", max_length=4000)
    answer: str = Field(default="", max_length=30000)
    citations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
