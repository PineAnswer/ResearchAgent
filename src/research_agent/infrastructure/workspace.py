from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeAssets:
    skill_paths: dict[str, str]
    memory_paths: list[str]


class WorkspaceBootstrapper:
    """Copy packaged Skills and long-term memory into the Deep Agents filesystem."""

    def __init__(self, filesystem_root: Path):
        self.filesystem_root = filesystem_root.resolve()
        self.package_root = Path(__file__).resolve().parents[1]

    def prepare(self) -> RuntimeAssets:
        self.filesystem_root.mkdir(parents=True, exist_ok=True)
        (self.filesystem_root / "papers").mkdir(parents=True, exist_ok=True)
        skill_paths: dict[str, str] = {}
        source_skills = self.package_root / "skills"
        destination_skills = self.filesystem_root / "skills"
        destination_skills.mkdir(parents=True, exist_ok=True)
        resolved_skills_root = destination_skills.resolve()

        for source in sorted(source_skills.iterdir()):
            if not source.is_dir():
                continue
            destination = (destination_skills / source.name).resolve()
            if not destination.is_relative_to(resolved_skills_root):
                raise ValueError(f"Skill destination escaped runtime workspace: {destination}")
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
            skill_paths[source.name] = f"/skills/{source.name}/"

        source_memory = self.package_root / "memories" / "AGENTS.md"
        destination_memory = self.filesystem_root / "memories" / "AGENTS.md"
        destination_memory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_memory, destination_memory)

        return RuntimeAssets(
            skill_paths=skill_paths,
            memory_paths=["/memories/AGENTS.md"],
        )
