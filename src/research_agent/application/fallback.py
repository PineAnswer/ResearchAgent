from __future__ import annotations

from research_agent.application.research_service import ResearchService


class OfflineFallback:
    """Create a traceable project when the model or network is unavailable."""

    def __init__(self, service: ResearchService):
        self.service = service

    def run(
        self,
        topic: str,
        research_question: str,
        reason: str,
        project_id: str | None = None,
    ) -> dict:
        project = None
        if project_id:
            try:
                project = self.service.get_project(project_id)
            except KeyError:
                project = None
        if project is None:
            project = self.service.create_project(topic, research_question)
        notice = self.service.save_artifact(
            project.project_id,
            "RuntimeFallback",
            {
                "reason": reason,
                "message": "模型或外部服务不可用；项目已创建，尚未生成科研结论。",
            },
        )
        return {
            "mode": "fallback",
            "reused_project": project_id == project.project_id,
            "project": project.model_dump(mode="json"),
            "notice": notice.model_dump(mode="json"),
        }
