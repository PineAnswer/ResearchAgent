from __future__ import annotations

import re
from typing import Any

from research_agent.application.artifact_normalization import normalize_artifact_payload
from research_agent.application.library_service import LibraryService
from research_agent.application.paper_ids import normalize_paper_id, same_paper_id
from research_agent.application.ports import ArtifactExporterPort, ResearchRepositoryPort
from research_agent.domain.models import (
    CandidateSetSnapshot,
    InsufficientEvidence,
    NarrativeReview,
    PaperCard,
    ResearchStage,
    ReviewOutline,
    ReviewResult,
    ReviewVerdict,
    ScreeningDecision,
    SearchReport,
    SearchFeedback,
    SectionDraft,
    SynthesisReport,
)


class WorkflowPrerequisiteError(ValueError):
    """Raised when a stage is requested before its required artifact exists."""


class InsufficientEvidenceError(WorkflowPrerequisiteError):
    """Raised when extraction produced no evidence that can support synthesis."""


ARTIFACT_SCHEMAS = {
    "SearchReport": SearchReport,
    "SupplementalSearchReport": SearchReport,
    "SearchFeedback": SearchFeedback,
    "CandidateSetSnapshot": CandidateSetSnapshot,
    "ScreeningDecision": ScreeningDecision,
    "PaperCard": PaperCard,
    "SynthesisReport": SynthesisReport,
    "ReviewResult": ReviewResult,
    "InsufficientEvidence": InsufficientEvidence,
    "ReviewOutline": ReviewOutline,
    "SectionDraft": SectionDraft,
    "NarrativeReview": NarrativeReview,
}

SYSTEM_ARTIFACTS = {"RuntimeFallback", "ScreeningLog"}

REQUIRED_ARTIFACTS = {
    ResearchStage.SEARCHED: "SearchReport",
    ResearchStage.SEARCH_REVIEW_PENDING: "CandidateSetSnapshot",
    ResearchStage.SCREENED: "ScreeningDecision",
    ResearchStage.EXTRACTED: "PaperCard",
    ResearchStage.SYNTHESIZED: "SynthesisReport",
    ResearchStage.REVIEWED: "ReviewResult",
    ResearchStage.OUTLINED: "ReviewOutline",
    ResearchStage.NARRATED: "NarrativeReview",
    ResearchStage.COMPLETED: "NarrativeReview",
    ResearchStage.INCONCLUSIVE: "InsufficientEvidence",
}

RECOVERABLE_OPERATIONAL_MARKERS = (
    "chief-editor",
    "narrative-writer",
    "research-outliner",
    "structured_response",
    "structured response",
    "subagent",
    "invalid result",
    "missing field",
    "timeout",
    "结构化",
    "无效结果",
    "缺少字段",
    "模型超时",
)


_NUMERIC_CLAIM_PATTERN = re.compile(
    r"(?<![\w])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:%|x|×|倍)?(?![\w])",
    flags=re.IGNORECASE,
)


def _numeric_claims(text: str) -> list[str]:
    """Extract normalized quantitative claims while ignoring model/term identifiers."""
    claims: list[str] = []
    for match in _NUMERIC_CLAIM_PATTERN.finditer(text):
        start, end = match.span()
        before = text[start - 1] if start else ""
        before_before = text[start - 2] if start > 1 else ""
        after = text[end] if end < len(text) else ""
        after_after = text[end + 1] if end + 1 < len(text) else ""
        # ResNet-50, GPT-4, FFL-3 and similar identifiers are names, not
        # quantitative claims. 2D/3DVG are already excluded by the regex's
        # word-boundary guards.
        if (before == "-" and before_before.isalpha()) or (
            after == "-" and after_after.isalpha()
        ):
            continue
        token = re.sub(r"\s+", "", match.group(0)).replace(",", "")
        token = token.replace("×", "x").replace("倍", "x").casefold()
        if token not in claims:
            claims.append(token)
    return claims


class ResearchService:
    """Single application-level API for all research project operations."""

    def __init__(
        self,
        repository: ResearchRepositoryPort,
        exporter: ArtifactExporterPort | None = None,
    ):
        self.repository = repository
        self.exporter = exporter
        self.library = LibraryService(repository)

    def _export_snapshot(self, project_id: str) -> None:
        if self.exporter is not None:
            self.exporter.export_snapshot(project_id, self.get_snapshot(project_id))

    def create_project(
        self,
        topic: str,
        research_question: str,
        *,
        user_id: str | None = None,
        conversation_id: str = "",
    ):
        project = self.repository.create_project(
            topic,
            research_question,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        self._export_snapshot(project.project_id)
        return project

    def create_conversation(self, topic: str, research_question: str):
        conversation, project = self.repository.create_conversation(
            topic,
            research_question,
        )
        self._export_snapshot(project.project_id)
        return conversation, project

    def get_conversation(self, conversation_id: str):
        return self.repository.get_conversation(conversation_id)

    def get_project_conversation(self, project_id: str):
        return self.repository.get_project_conversation(project_id)

    def list_conversations(self, limit: int = 50):
        return self.repository.list_conversations(limit)

    def get_project(self, project_id: str):
        return self.repository.get_project(project_id)

    def list_projects(self, limit: int = 20):
        return self.repository.list_projects(limit)

    def delete_project(self, project_id: str) -> None:
        self.repository.delete_project(project_id)
        if self.exporter is not None:
            self.exporter.delete_project(project_id)

    def get_snapshot(self, project_id: str) -> dict[str, Any]:
        snapshot = {
            "project": self.repository.get_project(project_id).model_dump(mode="json"),
            "artifacts": [
                item.model_dump(mode="json")
                for item in self.repository.list_artifacts(project_id)
            ],
            "events": [
                item.model_dump(mode="json") for item in self.repository.list_events(project_id)
            ],
        }
        try:
            conversation = self.repository.get_project_conversation(project_id)
        except KeyError:
            return snapshot
        runs = self.repository.list_conversation_runs(conversation.conversation_id)
        active_run = next(
            (run for run in runs if run.status in {"queued", "running"}),
            None,
        )
        snapshot.update(
            {
                "conversation": conversation.model_dump(mode="json"),
                "runs": [run.model_dump(mode="json") for run in runs],
                "active_run": (
                    active_run.model_dump(mode="json") if active_run is not None else None
                ),
                "messages": [
                    item.model_dump(mode="json")
                    for item in self.repository.list_conversation_messages(
                        conversation.conversation_id
                    )
                ],
            }
        )
        return snapshot

    def get_agent_context(self, project_id: str) -> dict[str, Any]:
        """Return only the current-stage artifacts a subagent needs."""
        project = self.repository.get_project(project_id)
        artifacts = self.repository.list_artifacts(project_id)
        kinds_by_stage = {
            ResearchStage.EXTRACTED: {"PaperCard"},
            ResearchStage.SYNTHESIZED: {"PaperCard", "SynthesisReport"},
            ResearchStage.REVIEW_PENDING: {"PaperCard", "SynthesisReport"},
            ResearchStage.REVIEWED: {"PaperCard", "SynthesisReport", "ReviewResult"},
            ResearchStage.OUTLINED: {"PaperCard", "ReviewOutline", "SectionDraft"},
            ResearchStage.NARRATED: {"PaperCard", "NarrativeReview"},
        }
        allowed_kinds = kinds_by_stage.get(project.stage, set())
        selected = []

        latest_outline = self._latest_artifact(artifacts, "ReviewOutline")
        latest_singletons = {
            kind: self._latest_artifact(artifacts, kind)
            for kind in {"SynthesisReport", "ReviewResult", "ReviewOutline", "NarrativeReview"}
        }
        latest_cards: dict[str, Any] = {}
        latest_drafts: dict[str, Any] = {}
        for artifact in artifacts:
            if artifact.kind == "PaperCard":
                paper_id = normalize_paper_id(str(artifact.payload.get("paper_id", "")))
                latest_cards[paper_id] = artifact
            elif (
                artifact.kind == "SectionDraft"
                and latest_outline is not None
                and artifact.artifact_id > latest_outline.artifact_id
            ):
                latest_drafts[str(artifact.payload.get("section_id", ""))] = artifact

        if "PaperCard" in allowed_kinds:
            selected.extend(latest_cards.values())
        if "SectionDraft" in allowed_kinds:
            selected.extend(latest_drafts.values())
        for kind, artifact in latest_singletons.items():
            if kind in allowed_kinds and artifact is not None:
                selected.append(artifact)

        selected.sort(key=lambda artifact: artifact.artifact_id or 0)
        return {
            "project": project.model_dump(mode="json"),
            "artifacts": [artifact.model_dump(mode="json") for artifact in selected],
        }

    @staticmethod
    def _latest_artifact(artifacts, kind: str):
        matches = [item for item in artifacts if item.kind == kind]
        return matches[-1] if matches else None

    @classmethod
    def _is_recoverable_operational_failure(cls, artifacts) -> bool:
        failure = cls._latest_artifact(artifacts, "InsufficientEvidence")
        if failure is None:
            return False
        payload = failure.payload
        details = "\n".join(
            [str(payload.get("reason", "")), str(payload.get("recommendation", ""))]
        ).casefold()
        return any(marker.casefold() in details for marker in RECOVERABLE_OPERATIONAL_MARKERS)

    @classmethod
    def _pass_review(cls, project, artifacts) -> ReviewResult:
        review_artifact = cls._latest_artifact(artifacts, "ReviewResult")
        review = (
            ReviewResult.model_validate(review_artifact.payload)
            if review_artifact is not None
            else project.current_review
        )
        if review is None or review.verdict is not ReviewVerdict.PASS:
            raise WorkflowPrerequisiteError(
                "Narrative continuation requires a saved PASS ReviewResult"
            )
        return review

    @classmethod
    def _operational_recovery_target(cls, project, artifacts) -> ResearchStage:
        review_artifact = cls._latest_artifact(artifacts, "ReviewResult")
        review = (
            ReviewResult.model_validate(review_artifact.payload)
            if review_artifact is not None
            else project.current_review
        )
        if review is not None and review.verdict is ReviewVerdict.PASS:
            if cls._latest_artifact(artifacts, "NarrativeReview") is not None:
                return ResearchStage.NARRATED
            if cls._latest_artifact(artifacts, "ReviewOutline") is not None:
                return ResearchStage.OUTLINED
            return ResearchStage.REVIEWED
        if review is not None and review.verdict is ReviewVerdict.REVISE:
            return ResearchStage.EXTRACTED
        if cls._latest_artifact(artifacts, "SynthesisReport") is not None:
            return ResearchStage.REVIEW_PENDING
        screening = cls._latest_artifact(artifacts, "ScreeningDecision")
        if screening is not None:
            included_ids = {
                normalize_paper_id(str(item))
                for item in screening.payload.get("included_paper_ids", [])
            }
            cards = cls._latest_paper_cards(artifacts)
            if (
                included_ids
                and included_ids.issubset(cards)
                and any(cards[paper_id].get("findings") for paper_id in included_ids)
            ):
                return ResearchStage.EXTRACTED
            return ResearchStage.SCREENED
        raise WorkflowPrerequisiteError(
            "Operational failure has no safe persisted stage to resume"
        )

    @classmethod
    def _validate_outline(cls, payload: dict[str, Any]) -> None:
        section_ids = [str(item.get("section_id", "")).strip() for item in payload["sections"]]
        if not section_ids or any(not section_id for section_id in section_ids):
            raise WorkflowPrerequisiteError(
                "ReviewOutline requires at least one non-empty section_id"
            )
        if len(section_ids) != len(set(section_ids)):
            raise WorkflowPrerequisiteError("ReviewOutline section_id values must be unique")

    @classmethod
    def _validate_section_draft(
        cls,
        artifacts,
        payload: dict[str, Any],
    ) -> None:
        outline = cls._latest_artifact(artifacts, "ReviewOutline")
        if outline is None:
            raise WorkflowPrerequisiteError("SectionDraft requires a saved ReviewOutline")
        section_id = payload["section_id"]
        outline_ids = {item["section_id"] for item in outline.payload["sections"]}
        if section_id not in outline_ids:
            raise WorkflowPrerequisiteError(
                f"SectionDraft section_id {section_id!r} is not in the latest ReviewOutline"
            )
        existing_ids = {
            item.payload.get("section_id")
            for item in artifacts
            if item.kind == "SectionDraft" and item.artifact_id > outline.artifact_id
        }
        if section_id in existing_ids:
            raise WorkflowPrerequisiteError(
                f"SectionDraft for {section_id!r} is already saved"
            )

    @classmethod
    def _validate_narrative_review(
        cls,
        artifacts,
        payload: dict[str, Any],
    ) -> None:
        section_ids = [str(item.get("section_id", "")).strip() for item in payload["sections"]]
        if not section_ids or any(not section_id for section_id in section_ids):
            raise WorkflowPrerequisiteError(
                "NarrativeReview requires at least one non-empty section_id"
            )
        if len(section_ids) != len(set(section_ids)):
            raise WorkflowPrerequisiteError("NarrativeReview section_id values must be unique")

        outline = cls._latest_artifact(artifacts, "ReviewOutline")
        if outline is None:
            raise WorkflowPrerequisiteError("NarrativeReview requires a saved ReviewOutline")
        outline_ids = {item["section_id"] for item in outline.payload["sections"]}
        draft_ids = {
            item.payload.get("section_id")
            for item in artifacts
            if item.kind == "SectionDraft" and item.artifact_id > outline.artifact_id
        }
        missing_drafts = sorted(outline_ids - draft_ids)
        if missing_drafts:
            raise WorkflowPrerequisiteError(
                "NarrativeReview requires one SectionDraft for every outline section; missing: "
                + ", ".join(missing_drafts)
            )
        missing_sections = sorted(outline_ids - set(section_ids))
        if missing_sections:
            raise WorkflowPrerequisiteError(
                "NarrativeReview omits outline sections: " + ", ".join(missing_sections)
            )

    @classmethod
    def _validate_completion(cls, artifacts) -> None:
        narrative = cls._latest_artifact(artifacts, "NarrativeReview")
        if narrative is None:
            raise WorkflowPrerequisiteError(
                "COMPLETED requires a saved NarrativeReview; continue the writing workflow"
            )
        section_ids = {
            str(item.get("section_id", "")).strip()
            for item in narrative.payload.get("sections", [])
            if str(item.get("section_id", "")).strip()
        }
        if not section_ids:
            raise WorkflowPrerequisiteError(
                "COMPLETED requires a NarrativeReview with at least one section"
            )

    def prepare_continuation(self, project_id: str) -> dict[str, Any]:
        """Return compact resume context and repair recoverable terminal states."""
        project = self.repository.get_project(project_id)
        if project.stage is ResearchStage.SCREENED:
            return {
                "mode": "screening",
                "project": project,
                "context": self.screening_context(project_id),
            }

        artifacts = self.repository.list_artifacts(project_id)
        recovered_from = None
        if project.stage in {ResearchStage.COMPLETED, ResearchStage.INCONCLUSIVE}:
            recovered_from = project.stage
            if project.stage is ResearchStage.COMPLETED:
                try:
                    self._validate_completion(artifacts)
                except WorkflowPrerequisiteError:
                    pass
                else:
                    raise WorkflowPrerequisiteError(
                        "Project is already complete and has a NarrativeReview"
                    )
            elif not self._is_recoverable_operational_failure(artifacts):
                raise WorkflowPrerequisiteError(
                    "INCONCLUSIVE can only be resumed after a recoverable operational failure"
                )

            recovery_stage = self._operational_recovery_target(project, artifacts)
            review_artifact = self._latest_artifact(artifacts, "ReviewResult")
            review = (
                ReviewResult.model_validate(review_artifact.payload)
                if review_artifact is not None
                else project.current_review
            )
            project = self.repository.reopen_interrupted_workflow(
                project_id,
                recovery_stage,
                actor="workflow-recovery",
                review=review,
            )
            self._export_snapshot(project_id)

        if project.stage is ResearchStage.SCREENED:
            return {
                "mode": "screening",
                "project": project,
                "context": self.screening_context(project_id),
            }

        pipeline_stages = {
            ResearchStage.EXTRACTED,
            ResearchStage.SYNTHESIZED,
            ResearchStage.REVIEW_PENDING,
        }
        if project.stage in pipeline_stages:
            return {
                "mode": "pipeline",
                "project": project,
                "context": {
                    "current_stage": project.stage.value,
                    "saved_context": self.get_agent_context(project_id),
                    "recovered_from": (
                        recovered_from.value if recovered_from is not None else None
                    ),
                },
            }

        resumable = {
            ResearchStage.REVIEWED,
            ResearchStage.OUTLINED,
            ResearchStage.NARRATED,
        }
        if project.stage not in resumable:
            raise WorkflowPrerequisiteError(
                "Project continuation requires SCREENED, EXTRACTED, SYNTHESIZED, "
                "REVIEW_PENDING, REVIEWED, OUTLINED, or NARRATED; "
                f"current stage is {project.stage.value}"
            )

        artifacts = self.repository.list_artifacts(project_id)
        review = self._pass_review(project, artifacts)
        outline = self._latest_artifact(artifacts, "ReviewOutline")
        narrative = self._latest_artifact(artifacts, "NarrativeReview")
        outline_id = outline.artifact_id if outline is not None else 0
        context = {
            "current_stage": project.stage.value,
            "review_verdict": review.verdict.value,
            "outline": outline.payload if outline is not None else None,
            "saved_section_draft_ids": [
                item.payload.get("section_id")
                for item in artifacts
                if item.kind == "SectionDraft" and item.artifact_id > outline_id
            ],
            "narrative_sections": [
                {
                    "section_id": item.get("section_id"),
                    "heading": item.get("heading"),
                }
                for item in (narrative.payload.get("sections", []) if narrative else [])
            ],
            "recovered_from": recovered_from.value if recovered_from is not None else None,
        }
        return {"mode": "narrative", "project": project, "context": context}

    def assemble_narrative_review(self, project_id: str):
        """Build a valid review from persisted drafts when editor formatting fails."""
        project = self.repository.get_project(project_id)
        if project.stage is not ResearchStage.OUTLINED:
            raise WorkflowPrerequisiteError(
                "Narrative assembly requires the project to be at OUTLINED"
            )

        artifacts = self.repository.list_artifacts(project_id)
        outline_artifact = self._latest_artifact(artifacts, "ReviewOutline")
        if outline_artifact is None:
            raise WorkflowPrerequisiteError("Narrative assembly requires a saved ReviewOutline")
        outline = ReviewOutline.model_validate(outline_artifact.payload)

        draft_artifacts = {}
        for item in artifacts:
            if item.kind != "SectionDraft" or item.artifact_id <= outline_artifact.artifact_id:
                continue
            draft_artifacts[str(item.payload.get("section_id", ""))] = item
        missing = [
            section.section_id
            for section in outline.sections
            if section.section_id not in draft_artifacts
        ]
        if missing:
            raise WorkflowPrerequisiteError(
                "Narrative assembly requires every saved outline draft; missing: "
                + ", ".join(missing)
            )

        sections = []
        evidence_chain: dict[str, list[str]] = {}
        for section in outline.sections:
            draft = SectionDraft.model_validate(
                draft_artifacts[section.section_id].payload
            )
            if not draft.content.strip():
                raise WorkflowPrerequisiteError(
                    f"SectionDraft {section.section_id!r} has no content"
                )
            cited_evidence = list(dict.fromkeys(draft.cited_evidence))
            narrative_section = {
                "section_id": draft.section_id,
                "heading": draft.heading or section.heading,
                "content": draft.content,
                "subsections": [],
                "cited_evidence": cited_evidence,
            }
            sections.append(narrative_section)
            for evidence_id in cited_evidence:
                evidence_chain.setdefault(evidence_id, []).append(section.section_id)

        candidate_metadata: dict[str, dict[str, Any]] = {}
        for artifact in artifacts:
            if artifact.kind not in {"SearchReport", "CandidateSetSnapshot"}:
                continue
            for candidate in artifact.payload.get("candidates", []):
                if not isinstance(candidate, dict):
                    continue
                paper_id = self._candidate_id(candidate)
                previous = candidate_metadata.get(paper_id, {})
                if sum(bool(value) for value in candidate.values()) >= sum(
                    bool(value) for value in previous.values()
                ):
                    candidate_metadata[paper_id] = candidate

        cards = self._latest_paper_cards(artifacts)
        references = []
        for paper_id, card in sorted(
            cards.items(), key=lambda item: str(item[1].get("title", "")).casefold()
        ):
            metadata = candidate_metadata.get(paper_id, {})
            authors = [
                str(author).strip()
                for author in metadata.get("authors", [])
                if str(author).strip()
            ]
            year = metadata.get("year")
            title = str(card.get("title") or metadata.get("title") or paper_id).strip()
            citation_parts = []
            if authors:
                citation_parts.append(", ".join(authors))
            if year:
                citation_parts.append(f"({year})")
            citation_parts.append(title.rstrip("."))
            citation_text = " ".join(citation_parts) + "."
            doi = str(metadata.get("doi") or "").strip()
            if doi:
                citation_text += f" DOI: {doi}."
            references.append(
                {
                    "paper_id": card.get("paper_id", paper_id),
                    "text": citation_text,
                    "bibtex": "",
                }
            )

        evidence_count = sum(len(card.get("findings", [])) for card in cards.values())
        abstract = (
            f"本综述围绕“{project.research_question}”展开，基于 {len(cards)} 篇入选文献的 "
            f"{evidence_count} 条可追踪证据，按照“{outline.narrative_arc}”组织为 "
            f"{len(sections)} 个章节。正文保留 evidence_id 引用，便于追踪论述与原始证据。"
        )
        body = "\n".join(section["content"] for section in sections)
        word_count = len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", body))
        payload = {
            "title": outline.title or project.topic,
            "abstract": abstract,
            "sections": sections,
            "references": references,
            "writing_style": outline.writing_style,
            "word_count": word_count,
            "evidence_chain": evidence_chain,
        }
        return self.save_artifact_and_transition(
            project_id,
            "NarrativeReview",
            payload,
            ResearchStage.COMPLETED,
            actor="chief-editor-fallback",
        )

    def screening_context(self, project_id: str) -> dict[str, Any]:
        """Return the small, authoritative context needed to continue from SCREENED."""
        artifacts = self.repository.list_artifacts(project_id)
        screenings = [item for item in artifacts if item.kind == "ScreeningDecision"]
        if not screenings:
            raise WorkflowPrerequisiteError(
                "Project continuation requires a ScreeningDecision artifact"
            )
        screening = screenings[-1]
        included_ids = [
            normalize_paper_id(item)
            for item in screening.payload.get("included_paper_ids", [])
        ]
        if not included_ids:
            raise WorkflowPrerequisiteError(
                "Project continuation requires ScreeningDecision.included_paper_ids"
            )

        candidates: list[dict[str, Any]] = []
        for artifact in artifacts:
            if artifact.kind not in {"CandidateSetSnapshot", "SearchReport"}:
                continue
            for candidate in artifact.payload.get("candidates", []):
                if isinstance(candidate, dict):
                    candidates.append(candidate)

        included_papers: list[dict[str, Any]] = []
        for paper_id in included_ids:
            matches = [
                candidate
                for candidate in candidates
                if same_paper_id(self._candidate_id(candidate), paper_id)
            ]
            match = max(
                matches,
                key=lambda candidate: sum(
                    bool(candidate.get(field))
                    for field in ("title", "abstract", "doi", "url", "authors", "year")
                ),
                default=None,
            )
            if match is None:
                raise WorkflowPrerequisiteError(
                    "ScreeningDecision includes papers missing from candidate metadata: "
                    + paper_id
                )
            normalized = {
                field: match.get(field)
                for field in (
                    "paper_id",
                    "title",
                    "authors",
                    "year",
                    "abstract",
                    "doi",
                    "url",
                    "source",
                    "library_id",
                )
            }
            normalized["paper_id"] = normalize_paper_id(
                str(normalized.get("paper_id", ""))
            )
            normalized["authors"] = normalized.get("authors") or []
            normalized["library_id"] = normalized.get("library_id") or ""
            included_papers.append(normalized)

        return {
            "screening_artifact_id": screening.artifact_id,
            "included_paper_ids": included_ids,
            "included_papers": included_papers,
            "saved_paper_card_ids": sorted(self._latest_paper_cards(artifacts)),
            "screening_reasons": screening.payload.get("reasons", []),
        }

    @staticmethod
    def _latest_paper_cards(artifacts) -> dict[str, dict[str, Any]]:
        cards: dict[str, dict[str, Any]] = {}
        for item in artifacts:
            if item.kind == "PaperCard":
                cards[normalize_paper_id(item.payload.get("paper_id", ""))] = item.payload
        return cards

    @staticmethod
    def _candidate_id(candidate: dict[str, Any]) -> str:
        return normalize_paper_id(
            candidate.get("paper_id")
            or candidate.get("doi")
            or f"title:{candidate.get('title', '')}"
        )

    @classmethod
    def _validate_screening_decision(cls, artifacts, payload: dict[str, Any]) -> None:
        snapshots = [item for item in artifacts if item.kind == "CandidateSetSnapshot"]
        if not snapshots:
            raise WorkflowPrerequisiteError(
                "ScreeningDecision requires a human-reviewed CandidateSetSnapshot"
            )
        available_ids = {
            cls._candidate_id(candidate)
            for candidate in snapshots[-1].payload.get("candidates", [])
        }
        included = [
            normalize_paper_id(item) for item in payload.get("included_paper_ids", [])
        ]
        excluded = [
            normalize_paper_id(item) for item in payload.get("excluded_paper_ids", [])
        ]
        if len(included) != len(set(included)):
            raise WorkflowPrerequisiteError("ScreeningDecision includes duplicate paper IDs")
        overlap = sorted(set(included) & set(excluded))
        if overlap:
            raise WorkflowPrerequisiteError(
                "ScreeningDecision includes and excludes the same papers: "
                + ", ".join(overlap)
            )
        unknown = sorted(set(included) - available_ids)
        if unknown:
            raise WorkflowPrerequisiteError(
                "ScreeningDecision includes papers outside the reviewed candidate set: "
                + ", ".join(unknown)
            )

    @classmethod
    def _evidence_index(cls, artifacts) -> dict[str, dict[str, Any]]:
        evidence: dict[str, dict[str, Any]] = {}
        for card in cls._latest_paper_cards(artifacts).values():
            for item in card.get("findings", []):
                evidence_id = str(item.get("evidence_id", ""))
                if evidence_id:
                    if evidence_id in evidence:
                        raise WorkflowPrerequisiteError(
                            f"Duplicate evidence_id across PaperCards: {evidence_id}"
                        )
                    evidence[evidence_id] = item
        return evidence

    @classmethod
    def _validate_synthesis_evidence(cls, artifacts, payload: dict[str, Any]) -> None:
        evidence = cls._evidence_index(artifacts)
        if not evidence:
            raise InsufficientEvidenceError(
                "Synthesis requires at least one traceable PaperCard finding"
            )

        referenced: set[str] = set()
        for group in ("consensus", "conflicts", "method_comparison"):
            for claim in payload.get(group, []):
                referenced.update(str(item) for item in claim.get("evidence_ids", []))
        for gap in payload.get("gaps", []):
            gap_ids = {str(item) for item in gap.get("evidence_ids", [])}
            referenced.update(gap_ids)
            supporting = {str(item) for item in gap.get("supporting_paper_ids", [])}
            evidence_papers = {
                str(evidence[item].get("paper_id", ""))
                for item in gap_ids
                if item in evidence
            }
            if evidence_papers and not evidence_papers.issubset(supporting):
                raise WorkflowPrerequisiteError(
                    "Gap supporting_paper_ids do not match referenced Evidence papers"
                )
            quote_text = " ".join(
                str(evidence[item].get("quote", "")) for item in gap_ids if item in evidence
            )
            numeric_tokens = _numeric_claims(str(gap.get("proposed_hypothesis", "")))
            supported_tokens = set(_numeric_claims(quote_text))
            unsupported = [token for token in numeric_tokens if token not in supported_tokens]
            if unsupported:
                raise WorkflowPrerequisiteError(
                    "Synthesis hypothesis contains unsupported numeric claims: "
                    + ", ".join(unsupported)
                )

        missing = sorted(referenced - set(evidence))
        if missing:
            raise WorkflowPrerequisiteError(
                "Synthesis references unknown evidence IDs: " + ", ".join(missing)
            )

    @classmethod
    def _validate_review_evidence(cls, artifacts, payload: dict[str, Any]) -> None:
        evidence_ids = set(cls._evidence_index(artifacts))
        verified = {str(item) for item in payload.get("verified_evidence_ids", [])}
        missing = sorted(verified - evidence_ids)
        if missing:
            raise WorkflowPrerequisiteError(
                "ReviewResult verifies unknown evidence IDs: " + ", ".join(missing)
            )
        if payload.get("verdict") == "PASS" and not verified:
            raise WorkflowPrerequisiteError(
                "A PASS review must include at least one verified evidence ID"
            )

    @staticmethod
    def _validate_artifact(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = normalize_artifact_payload(kind, payload)
        schema = ARTIFACT_SCHEMAS.get(kind)
        if schema is not None:
            return schema.model_validate(payload).model_dump(mode="json")
        if kind not in SYSTEM_ARTIFACTS:
            allowed = ", ".join([*ARTIFACT_SCHEMAS, *SYSTEM_ARTIFACTS])
            raise ValueError(f"Unsupported artifact kind {kind!r}; allowed: {allowed}")
        return payload

    def save_artifact(self, project_id: str, kind: str, payload: dict[str, Any]):
        stage_by_kind = {
            "PaperCard": {ResearchStage.SCREENED},
            "SectionDraft": {ResearchStage.OUTLINED},
        }
        project = self.repository.get_project(project_id)
        if kind in stage_by_kind:
            expected_stages = stage_by_kind[kind]
            if project.stage not in expected_stages:
                expected_text = ", ".join(sorted(stage.value for stage in expected_stages))
                raise WorkflowPrerequisiteError(
                    f"{kind} can only be saved while the project is at {expected_text}"
                )
        payload = self._validate_artifact(kind, payload)
        artifacts = self.repository.list_artifacts(project_id)
        if kind == "SectionDraft":
            self._validate_section_draft(artifacts, payload)
        elif kind == "PaperCard":
            evidence_ids = [str(item["evidence_id"]) for item in payload["findings"]]
            if len(evidence_ids) != len(set(evidence_ids)):
                raise WorkflowPrerequisiteError("PaperCard evidence_id values must be unique")
            mismatched = [
                item["evidence_id"]
                for item in payload["findings"]
                if not same_paper_id(item["paper_id"], payload["paper_id"])
            ]
            if mismatched:
                raise WorkflowPrerequisiteError(
                    "PaperCard Evidence paper_id mismatch: " + ", ".join(mismatched)
                )
            screenings = [item for item in artifacts if item.kind == "ScreeningDecision"]
            if not screenings:
                raise WorkflowPrerequisiteError(
                    "PaperCard requires a ScreeningDecision with included_paper_ids"
                )
            included_ids = {
                normalize_paper_id(item)
                for item in screenings[-1].payload["included_paper_ids"]
            }
            if normalize_paper_id(payload["paper_id"]) not in included_ids:
                raise WorkflowPrerequisiteError(
                    f"PaperCard {payload['paper_id']!r} is not included by ScreeningDecision"
                )
        artifact = self.repository.save_artifact(project_id, kind, payload)
        if self.exporter is not None:
            self.exporter.export_artifact(artifact)
        self._export_snapshot(project_id)
        return artifact

    def save_artifact_and_transition(
        self,
        project_id: str,
        kind: str,
        payload: dict[str, Any],
        target: ResearchStage,
        actor: str,
        review: ReviewResult | None = None,
    ):
        payload = self._validate_artifact(kind, payload)
        artifacts = self.repository.list_artifacts(project_id)
        project = self.repository.get_project(project_id)
        if kind == "SynthesisReport":
            self._validate_synthesis_evidence(artifacts, payload)
        elif kind == "ReviewResult":
            self._validate_review_evidence(artifacts, payload)
        elif kind == "ScreeningDecision":
            self._validate_screening_decision(artifacts, payload)
        elif kind == "ReviewOutline":
            self._validate_outline(payload)
        elif kind == "NarrativeReview":
            self._validate_narrative_review(artifacts, payload)
        if target is ResearchStage.COMPLETED and kind != "NarrativeReview":
            self._validate_completion(artifacts)
        required_kind = REQUIRED_ARTIFACTS.get(target)
        existing_kinds = {item.kind for item in artifacts}
        if required_kind is not None and required_kind != kind and required_kind not in existing_kinds:
            raise WorkflowPrerequisiteError(
                f"{target.value} requires a saved {required_kind} artifact"
            )
        artifact, project = self.repository.save_artifact_and_transition(
            project_id=project_id,
            kind=kind,
            payload=payload,
            target=target,
            actor=actor,
            review=review,
        )
        if self.exporter is not None:
            self.exporter.export_artifact(artifact)
        self._export_snapshot(project_id)
        return artifact, project

    def transition(
        self,
        project_id: str,
        target: ResearchStage,
        actor: str,
        review: ReviewResult | None = None,
    ):
        artifacts = self.repository.list_artifacts(project_id)
        if target is ResearchStage.COMPLETED:
            self._validate_completion(artifacts)
        required_kind = REQUIRED_ARTIFACTS.get(target)
        if required_kind is not None:
            existing_kinds = {item.kind for item in artifacts}
            if required_kind not in existing_kinds:
                raise WorkflowPrerequisiteError(
                    f"{target.value} requires a saved {required_kind} artifact"
                )
        if target is ResearchStage.EXTRACTED:
            screenings = [item for item in artifacts if item.kind == "ScreeningDecision"]
            if not screenings:
                raise WorkflowPrerequisiteError(
                    "EXTRACTED requires a ScreeningDecision with included_paper_ids"
                )
            included_ids = {
                normalize_paper_id(item)
                for item in screenings[-1].payload["included_paper_ids"]
            }
            if not included_ids:
                raise WorkflowPrerequisiteError(
                    "EXTRACTED requires included papers; finish as INCONCLUSIVE instead"
                )
            cards = self._latest_paper_cards(artifacts)
            saved_ids = set(cards)
            missing_ids = sorted(included_ids - saved_ids)
            if missing_ids:
                raise WorkflowPrerequisiteError(
                    "EXTRACTED requires one PaperCard for every included paper; missing: "
                    + ", ".join(missing_ids)
                )
            if not any(cards[paper_id].get("findings") for paper_id in included_ids):
                raise InsufficientEvidenceError(
                    "All included PaperCards have empty findings; finish as INCONCLUSIVE "
                    "before synthesis"
                )
        project = self.repository.transition(project_id, target, actor, review)
        self._export_snapshot(project_id)
        return project
