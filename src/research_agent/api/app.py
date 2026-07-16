from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.api.schemas import (
    ApiEnvelope,
    ContinueProjectRequest,
    ResearchRequest,
    SearchFeedbackRequest,
)
from research_agent.application.research_service import WorkflowPrerequisiteError
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.sqlite_repository import ProjectNotFound


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


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
    app.mount(
        "/ui-assets",
        StaticFiles(directory=FRONTEND_DIR),
        name="research-ui-assets",
    )

    @app.get("/", include_in_schema=False)
    async def research_console() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

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
                min_papers=request.min_papers,
                max_papers=request.max_papers,
                max_search_rounds=request.max_search_rounds,
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
                    min_papers=request.min_papers,
                    max_papers=request.max_papers,
                    max_search_rounds=request.max_search_rounds,
                ):
                    payload = json.dumps(_json_safe(event), ensure_ascii=False)
                    event_name = (
                        "awaiting_input"
                        if isinstance(event, dict)
                        and event.get("type") == "awaiting_input"
                        else "update"
                    )
                    yield f"event: {event_name}\ndata: {payload}\n\n"
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

    @app.get(
        "/api/projects",
        response_model=ApiEnvelope,
    )
    async def list_projects(
        limit: int = Query(default=20, ge=1, le=100),
    ) -> ApiEnvelope:
        projects = supervisor.service.list_projects(limit)
        return ApiEnvelope(data=[item.model_dump(mode="json") for item in projects])

    @app.get(
        "/api/projects/{project_id}",
        response_model=ApiEnvelope,
    )
    async def get_project(project_id: str) -> ApiEnvelope:
        try:
            snapshot = supervisor.service.get_snapshot(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        return ApiEnvelope(data=_json_safe(snapshot))

    @app.delete(
        "/api/projects/{project_id}",
        response_model=ApiEnvelope,
    )
    async def delete_project(project_id: str) -> ApiEnvelope:
        try:
            await asyncio.to_thread(supervisor.service.delete_project, project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        return ApiEnvelope(
            message="project_deleted",
            data={"project_id": project_id},
        )

    @app.get(
        "/api/projects/{project_id}/search-review",
        response_model=ApiEnvelope,
    )
    async def get_search_review(project_id: str) -> ApiEnvelope:
        try:
            result = supervisor.search_review.get_review(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except WorkflowPrerequisiteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiEnvelope(data=_json_safe(result))

    @app.post(
        "/api/projects/{project_id}/search-feedback",
        response_model=ApiEnvelope,
    )
    async def submit_search_feedback(
        project_id: str,
        request: SearchFeedbackRequest,
    ) -> ApiEnvelope:
        try:
            result = await asyncio.to_thread(
                supervisor.search_review.apply_feedback,
                project_id,
                request,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except WorkflowPrerequisiteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiEnvelope(data=_json_safe(result))

    @app.post(
        "/api/projects/{project_id}/continue",
        response_model=ApiEnvelope,
    )
    async def continue_project(
        project_id: str,
        request: ContinueProjectRequest,
    ) -> ApiEnvelope:
        try:
            result = await supervisor.acontinue_project(project_id, request.thread_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except WorkflowPrerequisiteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiEnvelope(data=_json_safe(result))

    return app


app = create_app()
