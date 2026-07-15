"""Backward-compatible Agent factory; new code should use agents.supervisor."""

from research_agent.agents.supervisor import ResearchSupervisor, build_research_agent

__all__ = ["ResearchSupervisor", "build_research_agent"]

