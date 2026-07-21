from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.tools import BaseTool

from research_agent.application.research_service import (
    ResearchService,
    WorkflowPrerequisiteError,
)
from research_agent.application.paper_ids import normalize_paper_id
from research_agent.domain.models import (
    CandidateSetSnapshot,
    ManualPaperInput,
    PaperCandidate,
    ResearchStage,
    SearchFeedback,
)


def _query_key(query: str) -> str:
    tokens = re.findall(r"[\w]+", query.casefold(), flags=re.UNICODE)
    normalized = {
        token[:-1] if token.endswith("s") and len(token) > 3 else token
        for token in tokens
    }
    return " ".join(sorted(normalized))


def _candidate_key(candidate: PaperCandidate) -> str:
    doi = str(candidate.doi or "").casefold().removeprefix("https://doi.org/").strip()
    if doi:
        return f"doi:{doi}"
    paper_id = normalize_paper_id(candidate.paper_id).casefold().strip()
    if paper_id:
        return f"id:{paper_id}"
    title = re.sub(r"\W+", " ", candidate.title.casefold()).strip()
    return f"title:{title}"


def _candidate_id(candidate: PaperCandidate) -> str:
    return normalize_paper_id(
        candidate.paper_id or str(candidate.doi or "") or f"title:{candidate.title}"
    )


class SearchReviewService:
    """Persist and apply human feedback while a project waits after retrieval."""

    def __init__(
        self,
        service: ResearchService,
        literature_tools: Mapping[str, BaseTool],
        *,
        max_rounds: int = 3,
        max_queries_per_round: int = 3,
        search_limit: int = 10,
        min_papers: int = 1,
        max_papers: int = 8,
        venue_index: Any | None = None,
    ) -> None:
        self.service = service
        self.tools = literature_tools
        self.max_rounds = max(0, max_rounds)
        self.max_queries_per_round = max(1, max_queries_per_round)
        self.search_limit = max(1, min(search_limit, 20))
        self.min_papers = max(1, min_papers)
        self.max_papers = max(self.min_papers, max_papers)
        self.venue_index = venue_index

    def _latest_snapshot(self, project_id: str) -> CandidateSetSnapshot:
        artifacts = self.service.repository.list_artifacts(
            project_id, "CandidateSetSnapshot"
        )
        if not artifacts:
            raise WorkflowPrerequisiteError(
                "Search review has no CandidateSetSnapshot; finish the initial search first"
            )
        return CandidateSetSnapshot.model_validate(artifacts[-1].payload)

    def _resolve_limits(
        self,
        snapshot: CandidateSetSnapshot | None = None,
        feedback: SearchFeedback | None = None,
    ) -> tuple[int, int, int]:
        min_papers = (
            feedback.min_papers
            if feedback is not None and feedback.min_papers is not None
            else snapshot.min_papers
            if snapshot is not None
            else self.min_papers
        )
        max_papers = (
            feedback.max_papers
            if feedback is not None and feedback.max_papers is not None
            else snapshot.max_papers
            if snapshot is not None
            else self.max_papers
        )
        max_rounds = (
            feedback.max_search_rounds
            if feedback is not None and feedback.max_search_rounds is not None
            else snapshot.max_search_rounds
            if snapshot is not None
            else self.max_rounds
        )
        if min_papers > max_papers:
            raise WorkflowPrerequisiteError(
                "min_papers cannot be greater than max_papers"
            )
        return min_papers, max_papers, max_rounds

    @staticmethod
    def _screening_status_from_snapshot(
        snapshot: CandidateSetSnapshot,
    ) -> dict[str, str]:
        status: dict[str, str] = {}
        for paper_id in snapshot.agent_included_paper_ids:
            status[paper_id] = "include"
        for paper_id in snapshot.agent_excluded_paper_ids:
            status[paper_id] = "exclude"
        for paper_id in snapshot.agent_uncertain_paper_ids:
            status.setdefault(paper_id, "uncertain")
        return status

    @staticmethod
    def _agent_review_fields(
        candidates: Sequence[PaperCandidate],
        *,
        decisions: Mapping[str, str],
        reasons: Mapping[str, str],
        min_papers: int,
        max_papers: int,
    ) -> dict[str, Any]:
        included: list[str] = []
        excluded: list[str] = []
        uncertain: list[str] = []
        reason_map: dict[str, str] = {}
        normalized_decisions = {
            normalize_paper_id(key): str(value).strip().casefold()
            for key, value in decisions.items()
        }
        normalized_reasons = {
            normalize_paper_id(key): str(value) for key, value in reasons.items()
        }
        for candidate in candidates:
            candidate_id = _candidate_id(candidate)
            doi = str(candidate.doi or "")
            decision = normalized_decisions.get(candidate_id) or normalized_decisions.get(
                doi
            )
            reason = normalized_reasons.get(candidate_id) or normalized_reasons.get(doi)
            if decision in {"include", "included", "accept", "selected", "pass"}:
                included.append(candidate_id)
                reason_map[candidate_id] = reason or "Agent marked this paper as relevant."
            elif decision in {"exclude", "excluded", "reject", "rejected", "fail"}:
                excluded.append(candidate_id)
                reason_map[candidate_id] = reason or "Agent marked this paper as out of scope."
            else:
                uncertain.append(candidate_id)
                reason_map[candidate_id] = reason or "Agent has not made a final screening decision."
        approved = min_papers <= len(included) <= max_papers and not uncertain
        if approved:
            note = (
                f"Agent screening approved {len(included)} papers within the "
                f"{min_papers}-{max_papers} paper limit."
            )
        else:
            note = (
                f"Agent screening has {len(included)} included, {len(uncertain)} "
                f"uncertain, {len(excluded)} excluded papers; target is "
                f"{min_papers}-{max_papers} included papers."
            )
        return {
            "agent_included_paper_ids": included,
            "agent_excluded_paper_ids": excluded,
            "agent_uncertain_paper_ids": uncertain,
            "agent_screening_reasons": reason_map,
            "agent_approved": approved,
            "agent_review_note": note,
        }

    def begin_review(
        self,
        project_id: str,
        *,
        min_papers: int | None = None,
        max_papers: int | None = None,
        max_search_rounds: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        quality_venues_only: bool = False,
    ) -> dict[str, Any]:
        project = self.service.get_project(project_id)
        if project.stage is not ResearchStage.SEARCHED:
            raise WorkflowPrerequisiteError(
                f"Search review can only start at SEARCHED; current stage is {project.stage.value}"
            )
        reports = self.service.repository.list_artifacts(project_id, "SearchReport")
        if not reports:
            raise WorkflowPrerequisiteError("Search review requires a SearchReport")
        report = reports[-1].payload
        config = SearchFeedback(
            action="refine",
            min_papers=min_papers,
            max_papers=max_papers,
            max_search_rounds=max_search_rounds,
        )
        min_papers, max_papers, max_rounds = self._resolve_limits(feedback=config)
        resolved_year_from = 2000 if year_from is None else year_from
        resolved_year_to = 2026 if year_to is None else year_to
        enforce_year_range = year_from is not None or year_to is not None
        candidates: list[PaperCandidate] = []
        filtered_candidates: list[PaperCandidate] = []
        filtered_candidate_reasons: dict[str, list[str]] = {}
        for item in report.get("candidates", []):
            payload = (
                self.venue_index.enrich_candidate(item)
                if self.venue_index is not None
                else item
            )
            candidate = PaperCandidate.model_validate(payload)
            reasons: list[str] = []
            if enforce_year_range and candidate.year is None:
                reasons.append("年份未知，不符合当前年份限制")
            elif enforce_year_range and not (
                resolved_year_from <= candidate.year <= resolved_year_to
            ):
                reasons.append(
                    f"发表年份 {candidate.year} 不在 "
                    f"{resolved_year_from}-{resolved_year_to} 范围内"
                )
            if quality_venues_only and (
                self.venue_index is None
                or not self.venue_index.qualifies_for_quality_filter(payload)
            ):
                reasons.append("未确认属于 CCF-A、JCR Q1 或 Nature Portfolio")
            if reasons:
                filtered_candidates.append(candidate)
                filtered_candidate_reasons[_candidate_id(candidate)] = reasons
            else:
                candidates.append(candidate)
        blocked_reason = ""
        if not candidates:
            blocked_reason = (
                f"检索到 {len(filtered_candidates)} 篇论文，但没有论文满足当前筛选条件。"
                "你可以从未达要求的论文中手动加入，补充 DOI，或调整检索条件。"
            )
        snapshot = CandidateSetSnapshot(
            candidates=candidates,
            filtered_candidates=filtered_candidates,
            filtered_candidate_reasons=filtered_candidate_reasons,
            blocked_reason=blocked_reason,
            executed_queries=report.get("search_terms", []),
            max_search_rounds=max_rounds,
            min_papers=min_papers,
            max_papers=max_papers,
            year_from=resolved_year_from,
            year_to=resolved_year_to,
            quality_venues_only=quality_venues_only,
            **self._agent_review_fields(
                candidates,
                decisions=report.get("screening_decisions", {}),
                reasons=report.get("screening_reasons", {}),
                min_papers=min_papers,
                max_papers=max_papers,
            ),
        )
        if candidates:
            artifact, project = self.service.save_artifact_and_transition(
                project_id,
                "CandidateSetSnapshot",
                snapshot.model_dump(mode="json"),
                ResearchStage.SEARCH_REVIEW_PENDING,
                actor="human-search-review",
            )
        else:
            artifact = self.service.save_artifact(
                project_id,
                "CandidateSetSnapshot",
                snapshot.model_dump(mode="json"),
            )
            project = self.service.get_project(project_id)
        return {
            "project": project.model_dump(mode="json"),
            "candidate_set": artifact.payload,
            "awaiting_input": bool(candidates),
            "manual_recovery_allowed": not candidates,
            "message": blocked_reason or "候选论文已准备好，等待人工审核。",
        }

    def get_review(self, project_id: str) -> dict[str, Any]:
        project = self.service.get_project(project_id)
        snapshot = self._latest_snapshot(project_id)
        artifacts = self.service.repository.list_artifacts(project_id)
        if not snapshot.candidates and not snapshot.filtered_candidates:
            reports = [item for item in artifacts if item.kind == "SearchReport"]
            if reports:
                legacy_filtered = [
                    PaperCandidate.model_validate(item)
                    for item in reports[-1].payload.get("candidates", [])
                ]
                if legacy_filtered:
                    legacy_reason = "未通过当前筛选条件（旧版记录未保存逐项原因）"
                    snapshot = snapshot.model_copy(
                        update={
                            "filtered_candidates": legacy_filtered,
                            "filtered_candidate_reasons": {
                                _candidate_id(item): [legacy_reason]
                                for item in legacy_filtered
                            },
                            "blocked_reason": (
                                f"检索到 {len(legacy_filtered)} 篇论文，但没有论文满足当前筛选条件。"
                                "你可以从未达要求的论文中手动加入，补充 DOI，或调整检索条件。"
                            ),
                        }
                    )
        feedbacks = [item for item in artifacts if item.kind == "SearchFeedback"]
        latest_action = feedbacks[-1].payload.get("action") if feedbacks else None
        reversible_stage = {
            "refine": ResearchStage.SEARCH_REVIEW_PENDING,
            "accept": ResearchStage.SCREENED,
            "stop": ResearchStage.INCONCLUSIVE,
        }.get(str(latest_action))
        can_undo = (
            len([item for item in artifacts if item.kind == "CandidateSetSnapshot"]) >= 2
            and reversible_stage is project.stage
        )
        return {
            "project": project.model_dump(mode="json"),
            "candidate_set": snapshot.model_dump(mode="json"),
            "awaiting_input": (
                project.stage is ResearchStage.SEARCH_REVIEW_PENDING
                and bool(snapshot.candidates)
            ),
            "manual_recovery_allowed": (
                not snapshot.candidates and bool(snapshot.filtered_candidates)
            ),
            "message": snapshot.blocked_reason or "候选论文已准备好，等待人工审核。",
            "can_undo": can_undo,
            "last_feedback_action": latest_action,
        }

    def undo_last_feedback(self, project_id: str) -> dict[str, Any]:
        """Append a compensating review snapshot and reopen reversible decisions."""
        project = self.service.get_project(project_id)
        artifacts = self.service.repository.list_artifacts(project_id)
        feedbacks = [item for item in artifacts if item.kind == "SearchFeedback"]
        snapshots = [item for item in artifacts if item.kind == "CandidateSetSnapshot"]
        if not feedbacks or len(snapshots) < 2:
            raise WorkflowPrerequisiteError("No search-review change is available to undo")
        latest_action = str(feedbacks[-1].payload.get("action", ""))
        expected_stage = {
            "refine": ResearchStage.SEARCH_REVIEW_PENDING,
            "accept": ResearchStage.SCREENED,
            "stop": ResearchStage.INCONCLUSIVE,
        }.get(latest_action)
        if expected_stage is None:
            raise WorkflowPrerequisiteError("The latest search-review change is not reversible")
        if project.stage is not expected_stage:
            raise WorkflowPrerequisiteError(
                f"Cannot undo {latest_action} from {project.stage.value}; expected {expected_stage.value}"
            )
        try:
            conversation = self.service.repository.get_project_conversation(project_id)
            active_run = self.service.repository.get_active_conversation_run(
                conversation.conversation_id
            )
        except KeyError:
            active_run = None
        if active_run is not None:
            raise WorkflowPrerequisiteError("Cannot undo while a continuation run is active")

        restored = CandidateSetSnapshot.model_validate(snapshots[-2].payload)
        self.service.save_artifact(
            project_id,
            "SearchFeedback",
            SearchFeedback(
                action="undo",
                comment=f"撤销上一项人工检索审核操作：{latest_action}",
            ).model_dump(mode="json"),
        )
        restored_artifact = self.service.save_artifact(
            project_id,
            "CandidateSetSnapshot",
            restored.model_dump(mode="json"),
        )
        if project.stage is not ResearchStage.SEARCH_REVIEW_PENDING:
            project = self.service.repository.reopen_interrupted_workflow(
                project_id,
                ResearchStage.SEARCH_REVIEW_PENDING,
                actor="human-search-review-undo",
            )
            self.service._export_snapshot(project_id)
        return {
            "project": project.model_dump(mode="json"),
            "candidate_set": restored_artifact.payload,
            "awaiting_input": True,
            "can_undo": False,
            "undone_action": latest_action,
        }

    def _search_queries(
        self,
        queries: Sequence[str],
        snapshot: CandidateSetSnapshot,
    ) -> tuple[list[PaperCandidate], list[str]]:
        candidates: list[PaperCandidate] = []
        failures: list[str] = []
        search_tool = self.tools["search_openalex"]
        for query in queries:
            raw = search_tool.invoke(
                {
                    "query": query,
                    "limit": self.search_limit,
                    "year_from": snapshot.year_from,
                    "year_to": snapshot.year_to,
                    "quality_venues_only": snapshot.quality_venues_only,
                }
            )
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                failures.append(f"{query}: {parsed.get('error_code', 'search_failed')}")
                continue
            if not isinstance(parsed, list):
                failures.append(f"{query}: invalid_search_response")
                continue
            candidates.extend(PaperCandidate.model_validate(item) for item in parsed)
        return candidates, failures

    def _resolve_manual_paper(self, paper: ManualPaperInput) -> PaperCandidate:
        if not paper.doi.strip():
            return PaperCandidate(
                paper_id=paper.paper_id,
                title=paper.title,
                authors=paper.authors,
                year=paper.year,
                # User-entered metadata may help identify a paper, but unverified
                # prose must never become downstream research evidence.
                abstract="",
                doi=None,
                url=paper.url,
                source="user-unverified",
            )

        raw = self.tools["verify_doi"].invoke({"doi": paper.doi})
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and parsed.get("ok") is False:
            raise WorkflowPrerequisiteError(
                f"DOI verification failed for {paper.doi}: "
                f"{parsed.get('error_code', 'unknown_error')}"
            )
        authors = []
        for author in parsed.get("authors", []):
            if isinstance(author, dict):
                authors.append(
                    " ".join(
                        filter(None, [author.get("given", ""), author.get("family", "")])
                    )
                )
            else:
                authors.append(str(author))
        doi = str(parsed.get("doi") or paper.doi).strip()
        return PaperCandidate(
            paper_id=paper.paper_id or doi,
            title=str(parsed.get("title") or paper.title),
            authors=authors or paper.authors,
            year=paper.year,
            abstract="",
            doi=doi,
            url=parsed.get("url") or paper.url,
            source="Crossref-user",
        )

    @staticmethod
    def _merge_candidates(
        current: Sequence[PaperCandidate],
        additions: Sequence[PaperCandidate],
        excluded_ids: set[str],
    ) -> list[PaperCandidate]:
        merged: dict[str, PaperCandidate] = {}
        for candidate in [*current, *additions]:
            candidate_id = _candidate_id(candidate)
            if candidate_id in excluded_ids:
                continue
            merged[_candidate_key(candidate)] = candidate
        return list(merged.values())

    def apply_feedback(self, project_id: str, feedback: SearchFeedback) -> dict[str, Any]:
        if feedback.action == "undo":
            return self.undo_last_feedback(project_id)
        project = self.service.get_project(project_id)
        snapshot = self._latest_snapshot(project_id)
        recovering_filtered_empty = (
            project.stage is ResearchStage.SEARCHED and bool(snapshot.blocked_reason)
        )
        if (
            project.stage is not ResearchStage.SEARCH_REVIEW_PENDING
            and not recovering_filtered_empty
        ):
            raise WorkflowPrerequisiteError(
                "Search feedback is only accepted at SEARCH_REVIEW_PENDING; "
                f"current stage is {project.stage.value}"
            )
        min_papers, max_papers, max_rounds = self._resolve_limits(snapshot, feedback)
        raw_queries = [item.strip() for item in feedback.suggested_queries if item.strip()]
        if len(raw_queries) > self.max_queries_per_round:
            raise WorkflowPrerequisiteError(
                f"At most {self.max_queries_per_round} suggested queries are allowed per round"
            )
        seen_queries = {_query_key(item) for item in snapshot.executed_queries}
        new_queries: list[str] = []
        for query in raw_queries:
            key = _query_key(query)
            if key and key not in seen_queries:
                seen_queries.add(key)
                new_queries.append(query)
        if new_queries and snapshot.search_round >= max_rounds:
            raise WorkflowPrerequisiteError(
                f"Search review round limit reached: {max_rounds}"
            )

        manual_candidates = [
            self._resolve_manual_paper(item) for item in feedback.added_papers
        ]
        searched_candidates, failures = self._search_queries(new_queries, snapshot)
        excluded_ids = {
            *(normalize_paper_id(item) for item in snapshot.excluded_paper_ids),
            *(
                normalize_paper_id(item)
                for item in feedback.excluded_paper_ids
                if item.strip()
            ),
        }
        for candidate in manual_candidates:
            excluded_ids.discard(_candidate_id(candidate))
        candidates = self._merge_candidates(
            snapshot.candidates,
            [*searched_candidates, *manual_candidates],
            excluded_ids,
        )
        added_ids = {_candidate_id(item) for item in manual_candidates}
        filtered_candidates = [
            item
            for item in snapshot.filtered_candidates
            if _candidate_id(item) not in added_ids
        ]
        filtered_candidate_reasons = {
            key: value
            for key, value in snapshot.filtered_candidate_reasons.items()
            if normalize_paper_id(key) not in {normalize_paper_id(item) for item in added_ids}
        }
        blocked_reason = ""
        if not candidates:
            blocked_reason = snapshot.blocked_reason or (
                "当前没有论文满足筛选条件。请手动加入论文、补充 DOI 或调整检索条件。"
            )
        comments = list(snapshot.user_comments)
        if feedback.comment.strip():
            comments.append(feedback.comment.strip())
        prior_status = self._screening_status_from_snapshot(snapshot)
        next_snapshot = CandidateSetSnapshot(
            candidates=candidates,
            filtered_candidates=filtered_candidates,
            filtered_candidate_reasons=filtered_candidate_reasons,
            blocked_reason=blocked_reason,
            excluded_paper_ids=sorted(excluded_ids),
            executed_queries=[*snapshot.executed_queries, *new_queries],
            search_round=snapshot.search_round + (1 if new_queries else 0),
            max_search_rounds=max_rounds,
            min_papers=min_papers,
            max_papers=max_papers,
            year_from=snapshot.year_from,
            year_to=snapshot.year_to,
            quality_venues_only=snapshot.quality_venues_only,
            **self._agent_review_fields(
                candidates,
                decisions=prior_status,
                reasons=snapshot.agent_screening_reasons,
                min_papers=min_papers,
                max_papers=max_papers,
            ),
            user_comments=comments,
            search_failures=[*snapshot.search_failures, *failures],
        )

        if feedback.action == "accept" and not candidates:
            raise WorkflowPrerequisiteError(
                "Cannot accept an empty candidate set; refine or stop the review"
            )
        if feedback.action == "accept" and not (
            min_papers <= len(candidates) <= max_papers
        ):
            raise WorkflowPrerequisiteError(
                f"Accepted paper count must be between {min_papers} and {max_papers}; "
                f"current count is {len(candidates)}"
            )

        feedback_artifact = self.service.save_artifact(
            project_id,
            "SearchFeedback",
            feedback.model_dump(mode="json"),
        )
        if new_queries:
            self.service.save_artifact(
                project_id,
                "SupplementalSearchReport",
                {
                    "query": " | ".join(new_queries),
                    "search_terms": new_queries,
                    "candidates": [
                        item.model_dump(mode="json") for item in searched_candidates
                    ],
                    "selection_notes": failures,
                },
            )
        snapshot_artifact = self.service.save_artifact(
            project_id,
            "CandidateSetSnapshot",
            next_snapshot.model_dump(mode="json"),
        )

        if recovering_filtered_empty and candidates:
            project = self.service.transition(
                project_id,
                ResearchStage.SEARCH_REVIEW_PENDING,
                actor="human-search-review-manual-recovery",
            )

        if feedback.action == "accept":
            included_ids = [_candidate_id(item) for item in candidates]
            screening, project = self.service.save_artifact_and_transition(
                project_id,
                "ScreeningDecision",
                {
                    "included_paper_ids": included_ids,
                    "excluded_paper_ids": sorted(excluded_ids),
                    "reasons": [
                        f"用户确认最终候选集，共{len(included_ids)}篇；"
                        f"补充检索{next_snapshot.search_round}轮。"
                    ],
                },
                ResearchStage.SCREENED,
                actor="human-search-review",
            )
            return {
                "project": project.model_dump(mode="json"),
                "candidate_set": snapshot_artifact.payload,
                "feedback_artifact_id": feedback_artifact.artifact_id,
                "screening": screening.model_dump(mode="json"),
                "awaiting_input": False,
                "ready_to_continue": True,
            }

        if feedback.action == "stop":
            insufficient, project = self.service.save_artifact_and_transition(
                project_id,
                "InsufficientEvidence",
                {
                    "reason": feedback.comment or "用户停止检索审核。",
                    "queries_attempted": next_snapshot.executed_queries,
                    "search_failures": next_snapshot.search_failures,
                    "recommendation": "根据用户反馈调整研究问题后重新创建任务。",
                },
                ResearchStage.INCONCLUSIVE,
                actor="human-search-review",
            )
            return {
                "project": project.model_dump(mode="json"),
                "candidate_set": snapshot_artifact.payload,
                "feedback_artifact_id": feedback_artifact.artifact_id,
                "insufficient_evidence": insufficient.model_dump(mode="json"),
                "awaiting_input": False,
            }

        return {
            "project": project.model_dump(mode="json"),
            "candidate_set": snapshot_artifact.payload,
            "feedback_artifact_id": feedback_artifact.artifact_id,
            "awaiting_input": bool(candidates),
            "manual_recovery_allowed": not candidates,
            "message": blocked_reason or "候选集已更新，等待人工审核。",
            "new_queries": new_queries,
            "search_failures": failures,
        }
