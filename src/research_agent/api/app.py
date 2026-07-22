from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from research_agent.agents.supervisor import ResearchSupervisor
from research_agent.api.background_runs import ConversationRunManager
from research_agent.api.schemas import (
    ApiEnvelope,
    ContinueProjectRequest,
    ConversationUpdateRequest,
    CreateConversationRequest,
    LibraryAssistantRequest,
    LibraryAttachmentRequest,
    LibraryBulkRequest,
    LibraryCollectionPaperRequest,
    LibraryCollectionRequest,
    LibraryImportRequest,
    LibraryMergeRequest,
    LibraryNoteRequest,
    PaperAnnotationRequest,
    PaperReadingProgressRequest,
    PaperQuestionRequest,
    ProjectAssistantRequest,
    ResearchNoteRequest,
    LibraryPaperRequest,
    LibraryPaperUpdateRequest,
    LibrarySelectionRequest,
    ProjectLibraryPaperRequest,
    ResearchRequest,
    SearchFeedbackRequest,
    SearchReviewSelectionRequest,
)
from research_agent.application.research_service import WorkflowPrerequisiteError
from research_agent.domain.models import LibraryAttachment, ResearchNote
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.sqlite_repository import (
    ActiveConversationRunError,
    ConversationNotFound,
    ConversationRunNotFound,
    LibraryCollectionNotFound,
    LibraryPaperNotFound,
    ProjectNotFound,
)


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
mimetypes.add_type("text/javascript", ".mjs")


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


def _compact_project_artifacts(data: dict[str, Any]) -> dict[str, Any]:
    """Keep project snapshots light; candidate details are served page by page."""
    for artifact in data.get("artifacts") or []:
        kind = artifact.get("kind")
        payload = artifact.get("payload")
        if not isinstance(payload, dict):
            continue
        if kind in {"SearchReport", "SupplementalSearchReport"}:
            candidates = payload.get("candidates") or []
            decisions = payload.get("screening_decisions") or {}
            payload["candidate_count"] = len(candidates)
            payload["screening_summary"] = {
                decision: sum(1 for value in decisions.values() if value == decision)
                for decision in ("include", "exclude", "uncertain")
            }
            payload["candidates"] = []
            payload["screening_decisions"] = {}
            payload["screening_reasons"] = {}
        elif kind == "CandidateSetSnapshot":
            candidates = payload.get("candidates") or []
            filtered = payload.get("filtered_candidates") or []
            payload["candidate_count"] = len(candidates)
            payload["filtered_candidate_count"] = len(filtered)
            payload["selected_count"] = len(payload.get("selected_paper_ids") or [])
            payload["candidates"] = []
            payload["filtered_candidates"] = []
            payload["filtered_candidate_reasons"] = {}
            payload["agent_screening_reasons"] = {}
    return data


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        load_dotenv()
    supervisor = ResearchSupervisor(settings)
    run_manager = ConversationRunManager(supervisor)

    def with_runtime_events(snapshot: Any) -> dict[str, Any]:
        data = _compact_project_artifacts(_json_safe(snapshot))
        active_run = data.get("active_run") or {}
        runs = data.get("runs") or []
        latest_run = active_run or (runs[0] if runs else {})
        data["runtime_events"] = run_manager.progress_events(latest_run.get("run_id"))
        return data

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await run_manager.shutdown()

    app = FastAPI(
        title="Evidence Research Agent",
        version="0.2.0",
        description="Deep Agents evidence-driven literature research API",
        lifespan=lifespan,
    )
    app.state.supervisor = supervisor
    app.state.run_manager = run_manager
    app.mount(
        "/ui-assets",
        StaticFiles(directory=FRONTEND_DIR),
        name="research-ui-assets",
    )

    @app.middleware("http")
    async def isolate_user_session(request: Request, call_next):
        raw_token = request.cookies.get("research_agent_session")
        user, new_token = supervisor.repository.resolve_user_session(
            raw_token,
            create_isolated_user=supervisor.settings.multi_user_mode,
        )
        request.state.user_id = user.user_id
        with supervisor.repository.user_scope(user.user_id):
            response = await call_next(request)
        if new_token:
            response.set_cookie(
                "research_agent_session",
                new_token,
                httponly=True,
                samesite="lax",
                secure=False,
                max_age=60 * 60 * 24 * 365,
            )
        return response

    @app.get("/", include_in_schema=False)
    async def research_console() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/health", response_model=ApiEnvelope)
    async def health() -> ApiEnvelope:
        return ApiEnvelope(
            data={
                "status": "ok" if supervisor.graph is not None else "degraded",
                "model": supervisor.settings.model,
                "provider": supervisor.settings.resolved_model()[0],
                "user_mode": (
                    "multi_user"
                    if supervisor.settings.multi_user_mode
                    else "local_shared"
                ),
                "agent_available": supervisor.graph is not None,
                "initialization_error": supervisor.initialization_error,
                "venue_rankings": supervisor.venue_rankings.stats(),
            }
        )

    @app.get("/api/venues/lookup", response_model=ApiEnvelope)
    async def lookup_venue(
        q: str = Query(min_length=1, max_length=300),
        venue_type: str | None = Query(default=None),
    ) -> ApiEnvelope:
        ranking = supervisor.venue_rankings.lookup(q, venue_type)
        return ApiEnvelope(
            message="venue_found" if ranking else "venue_not_found",
            data=ranking,
        )

    @app.get("/api/users/me", response_model=ApiEnvelope)
    async def current_user() -> ApiEnvelope:
        user = supervisor.repository.get_current_user()
        return ApiEnvelope(data=user.model_dump(mode="json"))

    @app.post("/api/conversations", response_model=ApiEnvelope, status_code=202)
    async def create_conversation(
        request: CreateConversationRequest,
        raw_request: Request,
    ) -> ApiEnvelope:
        if (
            request.min_papers is not None
            and request.max_papers is not None
            and request.min_papers > request.max_papers
        ):
            raise HTTPException(status_code=422, detail="min_papers_exceeds_max_papers")
        conversation, project = supervisor.service.create_conversation(
            request.topic,
            request.research_question,
        )
        try:
            run = await run_manager.start_initial(
                conversation.conversation_id,
                raw_request.state.user_id,
                min_papers=request.min_papers,
                max_papers=request.max_papers,
                max_search_rounds=request.max_search_rounds,
                year_from=request.year_from,
                year_to=request.year_to,
                prefer_library_search=request.prefer_library_search,
            )
        except ActiveConversationRunError as exc:
            raise HTTPException(status_code=409, detail="conversation_already_running") from exc
        snapshot = supervisor.service.get_snapshot(project.project_id)
        return ApiEnvelope(
            message="conversation_started",
            data={
                **with_runtime_events(snapshot),
                "run": run.model_dump(mode="json"),
            },
        )

    @app.get("/api/conversations", response_model=ApiEnvelope)
    async def list_conversations(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ApiEnvelope:
        rows = supervisor.service.list_conversations(limit)
        data = []
        for conversation in rows:
            active_run = supervisor.repository.get_active_conversation_run(
                conversation.conversation_id
            )
            project = supervisor.service.get_project(conversation.project_id)
            data.append(
                {
                    **conversation.model_dump(mode="json"),
                    "project": project.model_dump(mode="json"),
                    "active_run": (
                        active_run.model_dump(mode="json")
                        if active_run is not None
                        else None
                    ),
                }
            )
        return ApiEnvelope(data=data)

    @app.get("/api/conversations/{conversation_id}", response_model=ApiEnvelope)
    async def get_conversation(conversation_id: str) -> ApiEnvelope:
        try:
            conversation = supervisor.service.get_conversation(conversation_id)
            snapshot = supervisor.service.get_snapshot(conversation.project_id)
        except (ConversationNotFound, ProjectNotFound) as exc:
            raise HTTPException(status_code=404, detail="conversation_not_found") from exc
        return ApiEnvelope(data=with_runtime_events(snapshot))

    @app.patch("/api/conversations/{conversation_id}", response_model=ApiEnvelope)
    async def update_conversation(
        conversation_id: str,
        request: ConversationUpdateRequest,
    ) -> ApiEnvelope:
        try:
            conversation = await asyncio.to_thread(
                supervisor.service.update_conversation,
                conversation_id,
                title=request.title,
                pinned=request.pinned,
            )
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="conversation_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ApiEnvelope(
            message="conversation_updated",
            data=conversation.model_dump(mode="json"),
        )

    @app.delete("/api/conversations/{conversation_id}", response_model=ApiEnvelope)
    async def delete_conversation(conversation_id: str) -> ApiEnvelope:
        try:
            active_run = supervisor.repository.get_active_conversation_run(conversation_id)
            if active_run is not None:
                raise HTTPException(status_code=409, detail="conversation_is_running")
            conversation = supervisor.service.get_conversation(conversation_id)
            await asyncio.to_thread(
                supervisor.service.delete_conversation,
                conversation_id,
            )
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="conversation_not_found") from exc
        return ApiEnvelope(
            message="conversation_deleted",
            data={
                "conversation_id": conversation_id,
                "project_id": conversation.project_id,
            },
        )

    @app.post(
        "/api/conversations/{conversation_id}/continue",
        response_model=ApiEnvelope,
        status_code=202,
    )
    async def continue_conversation(
        conversation_id: str,
        request: Request,
    ) -> ApiEnvelope:
        try:
            run = await run_manager.start_continue(
                conversation_id,
                request.state.user_id,
            )
        except ConversationNotFound as exc:
            raise HTTPException(status_code=404, detail="conversation_not_found") from exc
        except ActiveConversationRunError as exc:
            raise HTTPException(status_code=409, detail="conversation_already_running") from exc
        return ApiEnvelope(
            message="conversation_resumed",
            data=run.model_dump(mode="json"),
        )

    @app.get("/api/runs/{run_id}", response_model=ApiEnvelope)
    async def get_conversation_run(run_id: str) -> ApiEnvelope:
        try:
            run = supervisor.repository.get_conversation_run(run_id)
        except ConversationRunNotFound as exc:
            raise HTTPException(status_code=404, detail="run_not_found") from exc
        return ApiEnvelope(data=run.model_dump(mode="json"))

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
                year_from=request.year_from,
                year_to=request.year_to,
                prefer_library_search=request.prefer_library_search,
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
                    year_from=request.year_from,
                    year_to=request.year_to,
                    prefer_library_search=request.prefer_library_search,
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
        data = []
        for project in projects:
            item = project.model_dump(mode="json")
            if project.conversation_id:
                try:
                    conversation = supervisor.service.get_conversation(
                        project.conversation_id
                    )
                    active_run = supervisor.repository.get_active_conversation_run(
                        conversation.conversation_id
                    )
                    item["conversation"] = conversation.model_dump(mode="json")
                    item["active_run"] = (
                        active_run.model_dump(mode="json")
                        if active_run is not None
                        else None
                    )
                except ConversationNotFound:
                    pass
            data.append(item)
        return ApiEnvelope(data=data)

    @app.get("/api/library", response_model=ApiEnvelope)
    async def list_library_papers(
        query: str = "",
        limit: int = Query(default=100, ge=1, le=500),
        view: str = "all",
        collection_id: str | None = None,
    ) -> ApiEnvelope:
        try:
            data = supervisor.service.library.list_papers(
                query,
                limit,
                view=view,
                collection_id=collection_id,
            )
        except LibraryCollectionNotFound as exc:
            raise HTTPException(status_code=404, detail="library_collection_not_found") from exc
        return ApiEnvelope(data=data)

    @app.get("/api/library/overview", response_model=ApiEnvelope)
    async def library_overview() -> ApiEnvelope:
        return ApiEnvelope(data=supervisor.service.library.library_overview())

    @app.post("/api/library/bulk", response_model=ApiEnvelope)
    async def bulk_library_action(request: LibraryBulkRequest) -> ApiEnvelope:
        try:
            changed = await asyncio.to_thread(
                supervisor.service.library.bulk_update,
                request.library_ids,
                request.action,
                request.value,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(message="library_bulk_updated", data={"library_ids": changed})

    @app.get("/api/library/duplicates", response_model=ApiEnvelope)
    async def library_duplicates() -> ApiEnvelope:
        return ApiEnvelope(data=supervisor.service.library.duplicate_groups())

    @app.post("/api/library/merge", response_model=ApiEnvelope)
    async def merge_library_papers(request: LibraryMergeRequest) -> ApiEnvelope:
        try:
            paper = await asyncio.to_thread(
                supervisor.service.library.merge_papers,
                request.primary_id,
                request.duplicate_id,
            )
        except (ValueError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(message="library_papers_merged", data=paper.model_dump(mode="json"))

    @app.post("/api/library/compare", response_model=ApiEnvelope)
    async def compare_library_papers(request: LibrarySelectionRequest) -> ApiEnvelope:
        try:
            data = await asyncio.to_thread(
                supervisor.service.library.compare_papers,
                request.library_ids,
            )
        except (ValueError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=data)

    @app.post("/api/library/assistant", response_model=ApiEnvelope)
    async def library_assistant(request: LibraryAssistantRequest) -> ApiEnvelope:
        try:
            data = await supervisor.answer_library_question(
                request.library_ids,
                request.question,
            )
        except (ValueError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=data)

    @app.get(
        "/api/library/papers/{library_id}/workspace",
        response_model=ApiEnvelope,
    )
    async def get_paper_workspace(library_id: str) -> ApiEnvelope:
        try:
            data = await asyncio.to_thread(
                supervisor.service.library.paper_workspace,
                library_id,
            )
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        return ApiEnvelope(data=data)

    @app.put(
        "/api/library/papers/{library_id}/reading-progress",
        response_model=ApiEnvelope,
    )
    async def save_paper_reading_progress(
        library_id: str,
        request: PaperReadingProgressRequest,
    ) -> ApiEnvelope:
        try:
            progress = await asyncio.to_thread(
                supervisor.service.library.save_reading_progress,
                library_id,
                page=request.page,
                attachment_id=request.attachment_id,
                project_id=request.project_id,
            )
        except (ValueError, KeyError, LibraryPaperNotFound, ProjectNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=progress.model_dump(mode="json"))

    @app.post(
        "/api/library/papers/{library_id}/workspace/acquire-full-text",
        response_model=ApiEnvelope,
    )
    async def acquire_paper_full_text(library_id: str) -> ApiEnvelope:
        try:
            result = await supervisor.acquire_library_full_text(library_id)
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(
            message="library_full_text_acquisition_finished",
            data=result,
        )

    @app.post(
        "/api/library/papers/{library_id}/workspace/question",
        response_model=ApiEnvelope,
    )
    async def ask_paper_question(
        library_id: str,
        request: PaperQuestionRequest,
    ) -> ApiEnvelope:
        try:
            data = await supervisor.answer_paper_question(
                library_id,
                request.question,
                scope=request.scope,
                attachment_id=request.attachment_id,
                page=request.page,
                selected_text=request.selected_text,
                prefix=request.prefix,
                suffix=request.suffix,
            )
        except (ValueError, KeyError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=data)

    @app.post(
        "/api/library/papers/{library_id}/workspace/reading-card",
        response_model=ApiEnvelope,
    )
    async def generate_paper_reading_card(
        library_id: str,
        attachment_id: str | None = None,
    ) -> ApiEnvelope:
        try:
            result = await supervisor.generate_library_reading_card(
                library_id,
                attachment_id,
            )
        except (ValueError, KeyError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(message="paper_reading_card_generated", data=result)

    @app.get(
        "/api/library/papers/{library_id}/workspace/report.md",
        response_class=PlainTextResponse,
    )
    async def export_paper_reading_report(library_id: str) -> PlainTextResponse:
        try:
            report = await asyncio.to_thread(
                supervisor.service.library.export_reading_report,
                library_id,
            )
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        return PlainTextResponse(
            report,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="reading-report-{library_id}.md"'
            },
        )

    @app.get(
        "/api/library/papers/{library_id}/annotations",
        response_model=ApiEnvelope,
    )
    async def list_paper_annotations(library_id: str) -> ApiEnvelope:
        try:
            annotations = await asyncio.to_thread(
                supervisor.repository.list_paper_annotations,
                library_id,
            )
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        return ApiEnvelope(data=[item.model_dump(mode="json") for item in annotations])

    @app.post(
        "/api/library/papers/{library_id}/annotations",
        response_model=ApiEnvelope,
    )
    async def create_paper_annotation(
        library_id: str,
        request: PaperAnnotationRequest,
    ) -> ApiEnvelope:
        try:
            annotation = await asyncio.to_thread(
                supervisor.service.library.save_annotation,
                library_id,
                request.model_dump(),
            )
        except (ValueError, KeyError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(
            message="paper_annotation_saved",
            data=annotation.model_dump(mode="json"),
        )

    @app.patch(
        "/api/library/papers/{library_id}/annotations/{annotation_id}",
        response_model=ApiEnvelope,
    )
    async def update_paper_annotation(
        library_id: str,
        annotation_id: str,
        request: PaperAnnotationRequest,
    ) -> ApiEnvelope:
        try:
            annotation = await asyncio.to_thread(
                supervisor.service.library.save_annotation,
                library_id,
                request.model_dump(),
                annotation_id=annotation_id,
            )
        except (ValueError, KeyError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=annotation.model_dump(mode="json"))

    @app.delete("/api/library/annotations/{annotation_id}", response_model=ApiEnvelope)
    async def delete_paper_annotation(annotation_id: str) -> ApiEnvelope:
        try:
            await asyncio.to_thread(
                supervisor.repository.delete_paper_annotation,
                annotation_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="paper_annotation_not_found") from exc
        return ApiEnvelope(
            message="paper_annotation_deleted",
            data={"annotation_id": annotation_id},
        )

    @app.post("/api/library/collections", response_model=ApiEnvelope)
    async def create_library_collection(request: LibraryCollectionRequest) -> ApiEnvelope:
        try:
            collection = await asyncio.to_thread(
                supervisor.service.library.create_collection,
                request.name,
                request.parent_id,
            )
        except (ValueError, LibraryCollectionNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(message="library_collection_created", data=collection.model_dump(mode="json"))

    @app.patch("/api/library/collections/{collection_id}", response_model=ApiEnvelope)
    async def update_library_collection(
        collection_id: str,
        request: LibraryCollectionRequest,
    ) -> ApiEnvelope:
        try:
            collection = await asyncio.to_thread(
                supervisor.service.library.update_collection,
                collection_id,
                name=request.name,
                parent_id=request.parent_id,
            )
        except (ValueError, StopIteration, LibraryCollectionNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=collection.model_dump(mode="json"))

    @app.delete("/api/library/collections/{collection_id}", response_model=ApiEnvelope)
    async def delete_library_collection(collection_id: str) -> ApiEnvelope:
        try:
            await asyncio.to_thread(
                supervisor.repository.delete_library_collection,
                collection_id,
            )
        except LibraryCollectionNotFound as exc:
            raise HTTPException(status_code=404, detail="library_collection_not_found") from exc
        return ApiEnvelope(message="library_collection_deleted", data={"collection_id": collection_id})

    @app.patch(
        "/api/library/collections/{collection_id}/papers/{library_id}",
        response_model=ApiEnvelope,
    )
    async def update_library_collection_paper(
        collection_id: str,
        library_id: str,
        request: LibraryCollectionPaperRequest,
    ) -> ApiEnvelope:
        try:
            relation = await asyncio.to_thread(
                supervisor.service.library.set_collection_paper_pinned,
                collection_id,
                library_id,
                pinned=request.pinned,
            )
        except (KeyError, LibraryCollectionNotFound, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=404, detail="library_collection_paper_not_found") from exc
        return ApiEnvelope(data=relation)

    @app.post("/api/library/papers", response_model=ApiEnvelope)
    async def add_library_paper(request: LibraryPaperRequest) -> ApiEnvelope:
        try:
            paper = await asyncio.to_thread(
                supervisor.service.library.upsert_paper,
                request.model_dump(),
                saved=True,
                tags=request.tags,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(
            message="library_paper_saved",
            data=paper.model_dump(mode="json"),
        )

    @app.get("/api/library/papers/{library_id}", response_model=ApiEnvelope)
    async def get_library_paper(library_id: str) -> ApiEnvelope:
        try:
            detail = supervisor.service.library.get_paper(library_id)
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        return ApiEnvelope(data=detail)

    @app.patch("/api/library/papers/{library_id}", response_model=ApiEnvelope)
    async def update_library_paper(
        library_id: str,
        request: LibraryPaperUpdateRequest,
    ) -> ApiEnvelope:
        changes = request.model_dump(exclude_none=True)
        try:
            paper = await asyncio.to_thread(
                supervisor.service.library.update_paper,
                library_id,
                changes,
            )
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=paper.model_dump(mode="json"))

    @app.delete("/api/library/papers/{library_id}", response_model=ApiEnvelope)
    async def archive_library_paper(library_id: str) -> ApiEnvelope:
        try:
            paper = await asyncio.to_thread(
                supervisor.repository.archive_library_paper,
                library_id,
            )
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        return ApiEnvelope(
            message="library_paper_archived",
            data=paper.model_dump(mode="json"),
        )

    @app.post("/api/library/papers/{library_id}/restore", response_model=ApiEnvelope)
    async def restore_library_paper(library_id: str) -> ApiEnvelope:
        try:
            paper = await asyncio.to_thread(
                supervisor.repository.restore_library_paper,
                library_id,
            )
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        return ApiEnvelope(message="library_paper_restored", data=paper.model_dump(mode="json"))

    @app.delete("/api/library/papers/{library_id}/permanent", response_model=ApiEnvelope)
    async def permanently_delete_library_paper(library_id: str) -> ApiEnvelope:
        try:
            await asyncio.to_thread(
                supervisor.repository.permanently_delete_library_paper,
                library_id,
            )
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiEnvelope(message="library_paper_deleted", data={"library_id": library_id})

    @app.post("/api/library/papers/{library_id}/notes", response_model=ApiEnvelope)
    async def add_library_note(
        library_id: str,
        request: LibraryNoteRequest,
    ) -> ApiEnvelope:
        try:
            note = await asyncio.to_thread(
                supervisor.service.library.add_note,
                library_id,
                request.content,
                request.project_id,
            )
        except (ValueError, LibraryPaperNotFound, ProjectNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(message="library_note_saved", data=note.model_dump(mode="json"))

    @app.patch("/api/library/papers/{library_id}/notes/{note_id}", response_model=ApiEnvelope)
    async def update_library_note(
        library_id: str,
        note_id: str,
        request: LibraryNoteRequest,
    ) -> ApiEnvelope:
        try:
            note = await asyncio.to_thread(
                supervisor.service.library.update_note,
                note_id,
                library_id,
                request.content,
            )
        except (ValueError, StopIteration, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=note.model_dump(mode="json"))

    @app.delete("/api/library/notes/{note_id}", response_model=ApiEnvelope)
    async def delete_library_note(note_id: str) -> ApiEnvelope:
        try:
            await asyncio.to_thread(supervisor.repository.delete_library_note, note_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="library_note_not_found") from exc
        return ApiEnvelope(message="library_note_deleted", data={"note_id": note_id})

    @app.post("/api/library/papers/{library_id}/attachments", response_model=ApiEnvelope)
    async def add_library_attachment(
        library_id: str,
        request: LibraryAttachmentRequest,
    ) -> ApiEnvelope:
        try:
            attachment = await asyncio.to_thread(
                supervisor.service.library.add_attachment,
                library_id,
                name=request.name,
                url=request.url,
                media_type=request.media_type,
            )
        except (ValueError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(message="library_attachment_saved", data=attachment.model_dump(mode="json"))

    @app.post(
        "/api/library/papers/{library_id}/attachments/upload",
        response_model=ApiEnvelope,
    )
    async def upload_library_attachment(
        library_id: str,
        request: Request,
        filename: str = Query(min_length=1, max_length=240),
        media_type: str = "application/pdf",
    ) -> ApiEnvelope:
        try:
            await asyncio.to_thread(supervisor.repository.get_library_paper, library_id)
        except LibraryPaperNotFound as exc:
            raise HTTPException(status_code=404, detail="library_paper_not_found") from exc
        content = await request.body()
        if not content:
            raise HTTPException(status_code=400, detail="attachment_is_empty")
        if len(content) > 30 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="attachment_exceeds_30_mb")
        safe_name = Path(filename).name.strip()
        if not safe_name:
            raise HTTPException(status_code=400, detail="invalid_attachment_name")
        attachment_id = f"LA-{uuid.uuid4().hex[:12]}"
        attachment_root = Path(supervisor.settings.data_dir) / "library-attachments"
        paper_dir = attachment_root / library_id
        file_path = paper_dir / attachment_id
        await asyncio.to_thread(paper_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(file_path.write_bytes, content)
        attachment = LibraryAttachment(
            attachment_id=attachment_id,
            library_id=library_id,
            name=safe_name,
            url=f"/api/library/attachments/{attachment_id}/content",
            media_type=media_type or "application/pdf",
            full_text_status="uploaded",
        )
        try:
            attachment = await asyncio.to_thread(
                supervisor.repository.save_library_attachment,
                attachment,
            )
        except Exception:
            file_path.unlink(missing_ok=True)
            raise
        ingestion = await supervisor.ingest_library_attachment(attachment.attachment_id)
        return ApiEnvelope(
            message="library_attachment_indexed"
            if ingestion["mode"] != "failed"
            else "library_attachment_uploaded_but_unreadable",
            data=ingestion["attachment"],
        )

    @app.post(
        "/api/library/attachments/{attachment_id}/ingest",
        response_model=ApiEnvelope,
    )
    async def ingest_library_attachment(attachment_id: str) -> ApiEnvelope:
        try:
            attachment = await asyncio.to_thread(
                supervisor.repository.get_library_attachment,
                attachment_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="library_attachment_not_found") from exc
        if not attachment.url.startswith("/api/library/attachments/"):
            raise HTTPException(status_code=400, detail="only_uploaded_pdf_can_be_ingested")
        result = await supervisor.ingest_library_attachment(attachment_id)
        return ApiEnvelope(message="library_attachment_ingestion_finished", data=result)

    @app.get("/api/library/attachments/{attachment_id}/content")
    async def get_library_attachment_content(attachment_id: str) -> FileResponse:
        try:
            attachment = await asyncio.to_thread(
                supervisor.repository.get_library_attachment,
                attachment_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="library_attachment_not_found") from exc
        try:
            file_path = await asyncio.to_thread(
                supervisor._library_attachment_path,
                attachment_id,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="library_attachment_content_not_found")
        return FileResponse(
            file_path,
            media_type=attachment.media_type,
            filename=attachment.name,
        )

    @app.delete("/api/library/attachments/{attachment_id}", response_model=ApiEnvelope)
    async def delete_library_attachment(attachment_id: str) -> ApiEnvelope:
        file_path: Path | None = None
        try:
            attachment = await asyncio.to_thread(
                supervisor.repository.get_library_attachment,
                attachment_id,
            )
            if attachment.url.startswith("/api/library/attachments/"):
                try:
                    file_path = await asyncio.to_thread(
                        supervisor._library_attachment_path,
                        attachment.attachment_id,
                    )
                except FileNotFoundError:
                    file_path = None
            await asyncio.to_thread(
                supervisor.repository.delete_library_attachment,
                attachment_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="library_attachment_not_found") from exc
        if file_path is not None:
            await asyncio.to_thread(file_path.unlink, missing_ok=True)
        return ApiEnvelope(message="library_attachment_deleted", data={"attachment_id": attachment_id})

    @app.post("/api/library/import", response_model=ApiEnvelope)
    async def import_library(request: LibraryImportRequest) -> ApiEnvelope:
        try:
            papers = await asyncio.to_thread(
                supervisor.service.library.import_records,
                request.content,
                request.format,
                request.tags,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(
            message="library_imported",
            data=[paper.model_dump(mode="json") for paper in papers],
        )

    @app.get("/api/library/export", response_class=PlainTextResponse)
    async def export_library(
        format: Literal["bibtex", "ris"] = "bibtex",
        query: str = "",
        ids: str = "",
    ) -> PlainTextResponse:
        library_ids = [item.strip() for item in ids.split(",") if item.strip()]
        content = supervisor.service.library.export_records(format, query, library_ids)
        media_type = "application/x-bibtex" if format == "bibtex" else "application/x-research-info-systems"
        extension = "bib" if format == "bibtex" else "ris"
        return PlainTextResponse(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="research-library.{extension}"'},
        )

    @app.get(
        "/api/projects/{project_id}",
        response_model=ApiEnvelope,
    )
    async def get_project(project_id: str) -> ApiEnvelope:
        try:
            project_library = await asyncio.to_thread(
                supervisor.service.library.sync_project,
                project_id,
            )
            snapshot = supervisor.service.get_snapshot(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        data = with_runtime_events(snapshot)
        data["project_library"] = project_library
        return ApiEnvelope(data=data)

    @app.post(
        "/api/projects/{project_id}/assistant",
        response_model=ApiEnvelope,
    )
    async def project_assistant(
        project_id: str,
        request: ProjectAssistantRequest,
    ) -> ApiEnvelope:
        try:
            data = await supervisor.answer_project_question(
                project_id,
                request.question,
                scope=request.scope,
                selected_text=request.selected_text,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except (ValueError, KeyError, LibraryPaperNotFound) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(data=data)

    @app.post("/api/projects/{project_id}/assistant/stream")
    async def stream_project_assistant(
        project_id: str,
        request: ProjectAssistantRequest,
    ) -> StreamingResponse:
        async def generate() -> AsyncIterator[str]:
            try:
                async for delta in supervisor.stream_project_chat(
                    project_id,
                    request.question,
                    scope=request.scope,
                    selected_text=request.selected_text,
                    history=[item.model_dump() for item in request.history],
                ):
                    payload = json.dumps({"text": delta}, ensure_ascii=False)
                    yield f"event: delta\ndata: {payload}\n\n"
                yield "event: done\ndata: {}\n\n"
            except ProjectNotFound:
                payload = json.dumps({"message": "project_not_found"}, ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"
            except Exception as exc:
                payload = json.dumps({"message": str(exc)}, ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/api/projects/{project_id}/notes",
        response_model=ApiEnvelope,
    )
    async def list_research_notes(project_id: str) -> ApiEnvelope:
        try:
            notes = await asyncio.to_thread(
                supervisor.repository.list_research_notes,
                project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        return ApiEnvelope(data=[note.model_dump(mode="json") for note in notes])

    @app.post(
        "/api/projects/{project_id}/notes",
        response_model=ApiEnvelope,
    )
    async def create_research_note(
        project_id: str,
        request: ResearchNoteRequest,
    ) -> ApiEnvelope:
        if request.kind in {"note", "annotation"} and not request.content.strip():
            raise HTTPException(status_code=400, detail="note_content_required")
        if request.kind == "annotation" and not request.selected_text.strip():
            raise HTTPException(status_code=400, detail="annotation_selection_required")
        if request.kind == "qa" and (not request.question.strip() or not request.answer.strip()):
            raise HTTPException(status_code=400, detail="qa_question_and_answer_required")
        note = ResearchNote(
            note_id=f"rnote-{uuid.uuid4().hex}",
            project_id=project_id,
            **request.model_dump(),
        )
        try:
            saved = await asyncio.to_thread(supervisor.repository.save_research_note, note)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        return ApiEnvelope(message="research_note_saved", data=saved.model_dump(mode="json"))

    @app.patch(
        "/api/projects/{project_id}/notes/{note_id}",
        response_model=ApiEnvelope,
    )
    async def update_research_note(
        project_id: str,
        note_id: str,
        request: ResearchNoteRequest,
    ) -> ApiEnvelope:
        try:
            existing = await asyncio.to_thread(
                supervisor.repository.get_research_note,
                note_id,
            )
            if existing.project_id != project_id:
                raise KeyError(note_id)
            updated = existing.model_copy(
                update={**request.model_dump(), "updated_at": datetime.now(UTC)}
            )
            saved = await asyncio.to_thread(
                supervisor.repository.save_research_note,
                updated,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="research_note_not_found") from exc
        return ApiEnvelope(message="research_note_updated", data=saved.model_dump(mode="json"))

    @app.delete(
        "/api/projects/{project_id}/notes/{note_id}",
        response_model=ApiEnvelope,
    )
    async def delete_research_note(project_id: str, note_id: str) -> ApiEnvelope:
        try:
            existing = await asyncio.to_thread(
                supervisor.repository.get_research_note,
                note_id,
            )
            if existing.project_id != project_id:
                raise KeyError(note_id)
            await asyncio.to_thread(supervisor.repository.delete_research_note, note_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="research_note_not_found") from exc
        return ApiEnvelope(message="research_note_deleted", data={"note_id": note_id})

    @app.get(
        "/api/projects/{project_id}/library",
        response_model=ApiEnvelope,
    )
    async def get_project_library(project_id: str) -> ApiEnvelope:
        try:
            papers = await asyncio.to_thread(
                supervisor.service.library.sync_project,
                project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        return ApiEnvelope(data=papers)

    @app.post(
        "/api/projects/{project_id}/library",
        response_model=ApiEnvelope,
    )
    async def save_project_library_paper(
        project_id: str,
        request: ProjectLibraryPaperRequest,
    ) -> ApiEnvelope:
        try:
            result = await asyncio.to_thread(
                supervisor.service.library.add_project_paper,
                project_id,
                request.model_dump(exclude={"status", "reason"}),
                status=request.status,
                reason=request.reason,
                saved=True,
                tags=request.tags,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ApiEnvelope(message="project_library_paper_saved", data=result)

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
    async def get_search_review(
        project_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=50),
        q: str = Query(default="", max_length=300),
        filtered_page: int = Query(default=1, ge=1),
    ) -> ApiEnvelope:
        try:
            result = await asyncio.to_thread(
                supervisor.search_review.get_review,
                project_id,
                page=page,
                page_size=page_size,
                query=q,
                filtered_page=filtered_page,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except WorkflowPrerequisiteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiEnvelope(data=_json_safe(result))

    @app.patch(
        "/api/projects/{project_id}/search-review/selection",
        response_model=ApiEnvelope,
    )
    async def update_search_review_selection(
        project_id: str,
        request: SearchReviewSelectionRequest,
    ) -> ApiEnvelope:
        try:
            result = await asyncio.to_thread(
                supervisor.search_review.update_selection,
                project_id,
                request.paper_ids,
                selected=request.selected,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except WorkflowPrerequisiteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiEnvelope(message="search_review_selection_updated", data=result)

    @app.post(
        "/api/projects/{project_id}/search-feedback",
        response_model=ApiEnvelope,
    )
    async def submit_search_feedback(
        project_id: str,
        feedback: SearchFeedbackRequest,
        raw_request: Request,
    ) -> ApiEnvelope:
        try:
            result = await asyncio.to_thread(
                supervisor.search_review.apply_feedback,
                project_id,
                feedback,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except WorkflowPrerequisiteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if feedback.action == "accept" and result.get("ready_to_continue"):
            project = supervisor.service.get_project(project_id)
            if project.conversation_id:
                try:
                    run = await run_manager.start_continue(
                        project.conversation_id,
                        raw_request.state.user_id,
                    )
                except ActiveConversationRunError as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="conversation_already_running",
                    ) from exc
                result = {
                    **result,
                    "research_started": True,
                    "run": run.model_dump(mode="json"),
                }
            else:
                result = {**result, "research_started": False, "run": None}
        return ApiEnvelope(data=_json_safe(result))

    @app.post(
        "/api/projects/{project_id}/search-feedback/undo",
        response_model=ApiEnvelope,
    )
    async def undo_search_feedback(project_id: str) -> ApiEnvelope:
        try:
            result = await asyncio.to_thread(
                supervisor.search_review.undo_last_feedback,
                project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="project_not_found") from exc
        except WorkflowPrerequisiteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApiEnvelope(message="search_feedback_undone", data=_json_safe(result))

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
