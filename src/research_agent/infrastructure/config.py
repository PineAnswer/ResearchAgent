from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str, default: bool = False) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model: str
    data_dir: Path
    database_path: Path
    filesystem_root: Path
    base_url: str | None = None
    aws_region: str | None = None
    aws_profile: str | None = None
    aws_credentials_csv: Path | None = None
    enable_fallback: bool = True
    multi_user_mode: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    openalex_api_key: str | None = None
    openalex_email: str | None = None
    max_openalex_searches: int = 3
    max_crossref_searches: int = 1
    max_paper_fetches_per_paper: int = 2
    search_max_retries: int = 3
    search_backoff_seconds: float = 1.0
    search_max_retry_wait_seconds: float = 30.0
    max_search_review_rounds: int = 3
    max_suggested_queries_per_round: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("RESEARCH_AGENT_DATA_DIR", ".research-agent")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        filesystem_root = data_dir / "filesystem"
        filesystem_root.mkdir(parents=True, exist_ok=True)
        return cls(
            model=os.getenv("RESEARCH_AGENT_MODEL", "openai:gpt-4.1-mini"),
            data_dir=data_dir,
            database_path=data_dir / "research_agent.db",
            filesystem_root=filesystem_root,
            base_url=os.getenv("RESEARCH_AGENT_BASE_URL") or None,
            aws_region=os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or os.getenv("RESEARCH_AGENT_AWS_REGION")
            or None,
            aws_profile=os.getenv("AWS_PROFILE") or None,
            aws_credentials_csv=(
                Path(csv_path).expanduser().resolve()
                if (csv_path := os.getenv("RESEARCH_AGENT_AWS_CREDENTIALS_CSV"))
                else None
            ),
            enable_fallback=_as_bool(
                os.getenv("RESEARCH_AGENT_ENABLE_FALLBACK", "true"),
                default=True,
            ),
            multi_user_mode=_as_bool(
                os.getenv("RESEARCH_AGENT_MULTI_USER_MODE", "false"),
                default=False,
            ),
            api_host=os.getenv("RESEARCH_AGENT_API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("RESEARCH_AGENT_API_PORT", "8000")),
            openalex_api_key=os.getenv("OPENALEX_API_KEY") or None,
            openalex_email=os.getenv("OPENALEX_EMAIL") or None,
            max_openalex_searches=max(
                1, int(os.getenv("RESEARCH_AGENT_MAX_OPENALEX_SEARCHES", "3"))
            ),
            max_crossref_searches=max(
                0, int(os.getenv("RESEARCH_AGENT_MAX_CROSSREF_SEARCHES", "1"))
            ),
            max_paper_fetches_per_paper=max(
                1, int(os.getenv("RESEARCH_AGENT_MAX_PAPER_FETCHES_PER_PAPER", "2"))
            ),
            search_max_retries=max(
                0, int(os.getenv("RESEARCH_AGENT_SEARCH_MAX_RETRIES", "3"))
            ),
            search_backoff_seconds=max(
                0.0, float(os.getenv("RESEARCH_AGENT_SEARCH_BACKOFF_SECONDS", "1.0"))
            ),
            search_max_retry_wait_seconds=max(
                0.0,
                float(os.getenv("RESEARCH_AGENT_SEARCH_MAX_RETRY_WAIT_SECONDS", "30.0")),
            ),
            max_search_review_rounds=max(
                0, int(os.getenv("RESEARCH_AGENT_MAX_SEARCH_REVIEW_ROUNDS", "3"))
            ),
            max_suggested_queries_per_round=max(
                1,
                int(os.getenv("RESEARCH_AGENT_MAX_SUGGESTED_QUERIES_PER_ROUND", "3")),
            ),
        )
