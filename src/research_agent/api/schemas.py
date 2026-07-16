from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from research_agent.domain.models import SearchFeedback


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    thread_id: str | None = None
    min_papers: int | None = Field(default=None, ge=1)
    max_papers: int | None = Field(default=None, ge=1)
    max_search_rounds: int | None = Field(default=None, ge=0)


class ApiEnvelope(BaseModel):
    code: int = 0
    message: str = "success"
    data: Any = None


class SearchFeedbackRequest(SearchFeedback):
    pass


class ContinueProjectRequest(BaseModel):
    thread_id: str | None = None
