from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.api.schemas import ApiEnvelope, ResearchRequest
from research_agent.infrastructure.config import Settings


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        load_dotenv()
    supervisor = ResearchSupervisor(settings)
    app = FastAPI(
        title="Evidence Research Agent",
        version="0.2.0",
        description="Deep Agents evidence-driven literature research API",
    )
    app.state.supervisor = supervisor

    @app.get("/health", response_model=ApiEnvelope)
    async def health() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                "status": "ok" if supervisor.graph is not None else "degraded",
                "model": supervisor.settings.model,
                "agent_available": supervisor.graph is not None,
                "initialization_error": supervisor.initialization_error,
            }
        )

    @app.post("/api/research/invoke", response_model=ApiEnvelope)
    async def invoke_research(request: ResearchRequest) -> ApiEnvelope:
        try:
            result = await supervisor.ainvoke(
                request.topic,
                request.research_question,
                request.thread_id,
            )
            return ApiEnvelope(data=_json_safe(result))
        except Exception as exc:
            if not supervisor.settings.enable_fallback or not supervisor.should_fallback(exc):
                raise
            result = supervisor.fallback.run(
                request.topic,
                request.research_question,
                reason=str(exc),
                project_id=getattr(exc, "project_id", None),
            )
            return ApiEnvelope(message="fallback", data=result)

    @app.post("/api/research/stream")
    async def stream_research(request: ResearchRequest) -> StreamingResponse:
        async def generate() -> AsyncIterator[str]:
            try:
                async for event in supervisor.astream(
                    request.topic,
                    request.research_question,
                    request.thread_id,
                ):
                    payload = json.dumps(_json_safe(event), ensure_ascii=False)
                    yield f"event: update\ndata: {payload}\n\n"
                yield "event: done\ndata: {}\n\n"
            except Exception as exc:
                if supervisor.settings.enable_fallback and supervisor.should_fallback(exc):
                    fallback = supervisor.fallback.run(
                        request.topic,
                        request.research_question,
                        reason=str(exc),
                        project_id=getattr(exc, "project_id", None),
                    )
                    payload = json.dumps(_json_safe(fallback), ensure_ascii=False)
                    yield f"event: fallback\ndata: {payload}\n\n"
                else:
                    payload = json.dumps({"message": str(exc)}, ensure_ascii=False)
                    yield f"event: error\ndata: {payload}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


app = create_app()
