from research_agent.infrastructure.config import Settings


def test_search_settings_are_configurable_from_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENALEX_API_KEY", "oa-test-key")
    monkeypatch.setenv("OPENALEX_EMAIL", "researcher@example.com")
    monkeypatch.setenv("RESEARCH_AGENT_MAX_OPENALEX_SEARCHES", "5")
    monkeypatch.setenv("RESEARCH_AGENT_MAX_CROSSREF_SEARCHES", "2")
    monkeypatch.setenv("RESEARCH_AGENT_MAX_PAPER_FETCHES_PER_PAPER", "4")
    monkeypatch.setenv("RESEARCH_AGENT_SEARCH_MAX_RETRIES", "4")
    monkeypatch.setenv("RESEARCH_AGENT_SEARCH_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("RESEARCH_AGENT_SEARCH_MAX_RETRY_WAIT_SECONDS", "12")
    monkeypatch.setenv("RESEARCH_AGENT_MAX_SEARCH_REVIEW_ROUNDS", "4")
    monkeypatch.setenv("RESEARCH_AGENT_MAX_SUGGESTED_QUERIES_PER_ROUND", "2")
    monkeypatch.setenv("RESEARCH_AGENT_MAX_DEEP_READ_PAPERS", "12")
    monkeypatch.setenv("RESEARCH_AGENT_GRAPH_RECURSION_LIMIT", "640")
    monkeypatch.setenv("RESEARCH_AGENT_MULTI_USER_MODE", "true")
    monkeypatch.setenv("RESEARCH_AGENT_AWS_REGION", "us-west-2")
    monkeypatch.setenv("RESEARCH_AGENT_AWS_CREDENTIALS_CSV", str(tmp_path / "aws.csv"))

    settings = Settings.from_env()

    assert settings.openalex_api_key == "oa-test-key"
    assert settings.openalex_email == "researcher@example.com"
    assert settings.max_openalex_searches == 5
    assert settings.max_crossref_searches == 2
    assert settings.max_paper_fetches_per_paper == 4
    assert settings.search_max_retries == 4
    assert settings.search_backoff_seconds == 0.5
    assert settings.search_max_retry_wait_seconds == 12
    assert settings.max_search_review_rounds == 4
    assert settings.max_suggested_queries_per_round == 2
    assert settings.max_deep_read_papers == 12
    assert settings.graph_recursion_limit == 640
    assert settings.multi_user_mode is True
    assert settings.aws_region == "us-west-2"
    assert settings.aws_credentials_csv == (tmp_path / "aws.csv").resolve()
