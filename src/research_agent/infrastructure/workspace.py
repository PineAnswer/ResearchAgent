from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


def validate_skill_content(skill_name: str, content: str, skill_file: Path) -> None:
    """Validate the minimal frontmatter needed for deterministic role mapping."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Skill frontmatter is missing: {skill_file}")
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise ValueError(f"Skill frontmatter is not closed: {skill_file}") from exc

    metadata: dict[str, str] = {}
    for line in lines[1:closing_index]:
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip()
    declared_name = metadata.get("name", "")
    if declared_name != skill_name:
        raise ValueError(
            f"Skill name {declared_name!r} does not match directory {skill_name!r}: "
            f"{skill_file}"
        )
    if not metadata.get("description"):
        raise ValueError(f"Skill description is missing: {skill_file}")


@dataclass(frozen=True)
class RuntimeAssets:
    skill_paths: dict[str, str]
    skill_contents: dict[str, str]
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
        skill_contents: dict[str, str] = {}
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
            skill_file = destination / "SKILL.md"
            if not skill_file.is_file():
                raise FileNotFoundError(f"Skill file is missing: {skill_file}")
            content = skill_file.read_text(encoding="utf-8").strip()
            if not content:
                raise ValueError(f"Skill file is empty: {skill_file}")
            validate_skill_content(source.name, content, skill_file)
            skill_paths[source.name] = f"/skills/{source.name}/"
            skill_contents[source.name] = content

        source_memory = self.package_root / "memories" / "AGENTS.md"
        destination_memory = self.filesystem_root / "memories" / "AGENTS.md"
        destination_memory.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_memory, destination_memory)

        return RuntimeAssets(
            skill_paths=skill_paths,
            skill_contents=skill_contents,
            memory_paths=["/memories/AGENTS.md"],
        )
