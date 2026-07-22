from pathlib import Path


def test_frontend_exposes_failed_run_and_retry_state() -> None:
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    assert "function latestFailedRun(snapshot)" in script
    assert '["failed", "interrupted"].includes(latestRun?.status)' in script
    assert 'title = "研究任务运行失败"' in script
    assert 'elements.stageBadge.textContent = "运行失败"' in script
    assert 'return "retry"' in script
    assert 'elements.continueButtonLabel.textContent = "重新运行"' in script
    assert "研究执行失败：${failedRun.error" in script


def test_frontend_shows_search_rounds_without_duplicate_elapsed_time() -> None:
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")
    styles = Path("src/research_agent/api/frontend/styles.css").read_text(encoding="utf-8")

    assert 'if (current) return "进行中"' in script
    assert "addActivity(message || RUN_PHASES[activePhase].detail)" not in script
    assert "function appendRuntimeEvent(event" in script
    assert "第 ${data.round || 1} 轮检索" in script
    assert "检索词：${data.queries.join" in script
    assert "function sourceRoundSummary" in script
    assert "检索综合完成：${data.rounds" in script
    assert ".activity-details" in styles
