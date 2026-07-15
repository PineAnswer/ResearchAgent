from __future__ import annotations

import re
from typing import Any

from research_agent.application.artifact_normalization import normalize_artifact_payload
from research_agent.application.ports import ArtifactExporterPort, ResearchRepositoryPort
from research_agent.domain.models import (
    InsufficientEvidence,
    PaperCard,
    ResearchStage,
    ReviewResult,
    ScreeningDecision,
    SearchReport,
    SynthesisReport,
)


class WorkflowPrerequisiteError(ValueError):
    """Raised when a stage is requested before its required artifact exists."""


class InsufficientEvidenceError(WorkflowPrerequisiteError):
    """Raised when extraction produced no evidence that can support synthesis."""


ARTIFACT_SCHEMAS = {
    "SearchReport": SearchReport,
    "ScreeningDecision": ScreeningDecision,
    "PaperCard": PaperCard,
    "SynthesisReport": SynthesisReport,
    "ReviewResult": ReviewResult,
    "InsufficientEvidence": InsufficientEvidence,
}

SYSTEM_ARTIFACTS = {"RuntimeFallback", "ScreeningLog"}

REQUIRED_ARTIFACTS = {
    ResearchStage.SEARCHED: "SearchReport",
    ResearchStage.SCREENED: "ScreeningDecision",
    ResearchStage.EXTRACTED: "PaperCard",
    ResearchStage.SYNTHESIZED: "SynthesisReport",
    ResearchStage.REVIEWED: "ReviewResult",
    ResearchStage.INCONCLUSIVE: "InsufficientEvidence",
}


class ResearchService:
    """Single application-level API for all research project operations."""

    def __init__(
        self,
        repository: ResearchRepositoryPort,
        exporter: ArtifactExporterPort | None = None,
    ):
        self.repository = repository
        self.exporter = exporter

    def _export_snapshot(self, project_id: str) -> None:
        if self.exporter is not None:
            self.exporter.export_snapshot(project_id, self.get_snapshot(project_id))

    def create_project(self, topic: str, research_question: str):
        project = self.repository.create_project(topic, research_question)
        self._export_snapshot(project.project_id)
        return project

    def get_project(self, project_id: str):
        return self.repository.get_project(project_id)

    def get_snapshot(self, project_id: str) -> dict[str, Any]:
        return {
            "project": self.repository.get_project(project_id).model_dump(mode="json"),
            "artifacts": [
                item.model_dump(mode="json")
                for item in self.repository.list_artifacts(project_id)
            ],
            "events": [
                item.model_dump(mode="json") for item in self.repository.list_events(project_id)
            ],
        }

    @staticmethod
    def _latest_paper_cards(artifacts) -> dict[str, dict[str, Any]]:
        cards: dict[str, dict[str, Any]] = {}
        for item in artifacts:
            if item.kind == "PaperCard":
                cards[str(item.payload.get("paper_id", ""))] = item.payload
        return cards

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
            quote_text = " ".join(str(evidence[item].get("quote", "")) for item in gap_ids if item in evidence)
            numeric_tokens = re.findall(r"\d+(?:\.\d+)?%?", gap.get("proposed_hypothesis", ""))
            unsupported = [token for token in numeric_tokens if token not in quote_text]
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
        if kind == "PaperCard":
            project = self.repository.get_project(project_id)
            if project.stage is not ResearchStage.SCREENED:
                raise WorkflowPrerequisiteError(
                    "PaperCard can only be saved while the project is at SCREENED"
                )
        payload = self._validate_artifact(kind, payload)
        if kind == "PaperCard":
            evidence_ids = [str(item["evidence_id"]) for item in payload["findings"]]
            if len(evidence_ids) != len(set(evidence_ids)):
                raise WorkflowPrerequisiteError("PaperCard evidence_id values must be unique")
            mismatched = [
                item["evidence_id"]
                for item in payload["findings"]
                if item["paper_id"] != payload["paper_id"]
            ]
            if mismatched:
                raise WorkflowPrerequisiteError(
                    "PaperCard Evidence paper_id mismatch: " + ", ".join(mismatched)
                )
            artifacts = self.repository.list_artifacts(project_id)
            screenings = [item for item in artifacts if item.kind == "ScreeningDecision"]
            included_ids = set(screenings[-1].payload["included_paper_ids"])
            if payload["paper_id"] not in included_ids:
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
        if kind == "SynthesisReport":
            self._validate_synthesis_evidence(artifacts, payload)
        elif kind == "ReviewResult":
            self._validate_review_evidence(artifacts, payload)
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
        required_kind = REQUIRED_ARTIFACTS.get(target)
        if required_kind is not None:
            existing_kinds = {item.kind for item in artifacts}
            if required_kind not in existing_kinds:
                raise WorkflowPrerequisiteError(
                    f"{target.value} requires a saved {required_kind} artifact"
                )
        if target is ResearchStage.EXTRACTED:
            screenings = [item for item in artifacts if item.kind == "ScreeningDecision"]
            included_ids = set(screenings[-1].payload["included_paper_ids"])
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
