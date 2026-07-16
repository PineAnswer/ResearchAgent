from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from research_agent.domain.models import ArtifactRecord


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return cleaned or "artifact"


class JsonArtifactExporter:
    """Mirror database artifacts into human-inspectable UTF-8 JSON files."""

    def __init__(self, output_root: str | Path):
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)

    def export_artifact(self, artifact: ArtifactRecord) -> None:
        artifact_id = artifact.artifact_id or 0
        filename = f"{artifact_id:06d}-{_safe_filename(artifact.kind)}.json"
        path = self.output_root / artifact.project_id / "artifacts" / filename
        self._write_json(path, artifact.model_dump(mode="json"))

    def export_snapshot(self, project_id: str, snapshot: dict[str, Any]) -> None:
        project_root = self.output_root / project_id
        self._write_json(project_root / "snapshot.json", snapshot)
        self._write_json(project_root / "project.json", snapshot["project"])
        self._write_json(project_root / "state-events.json", snapshot["events"])

    def delete_project(self, project_id: str) -> None:
        project_root = (self.output_root / project_id).resolve()
        if project_root.parent != self.output_root:
            raise ValueError("project_id resolves outside the output directory")
        shutil.rmtree(project_root, ignore_errors=True)
