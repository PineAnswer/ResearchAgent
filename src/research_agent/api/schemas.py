from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    thread_id: str | None = None


class ApiEnvelope(BaseModel):
    code: int = 0
    message: str = "success"
    data: Any = None

