from pathlib import Path


def test_search_review_hides_internal_limits_and_review_comment() -> None:
    markup = Path("src/research_agent/api/frontend/index.html").read_text(
        encoding="utf-8"
    )
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    assert "审核备注" not in markup
    assert 'id="feedbackComment"' not in markup
    assert 'id="minPapers"' not in markup
    assert 'id="maxPapers"' not in markup
    assert 'id="initialMinPapers"' not in markup
    assert 'id="initialMaxPapers"' not in markup
    assert "系统精读容量" in markup
    assert "paperCapacity" in script
    assert "系统单次最多精读" in script
    assert "min_papers:" not in script
    assert "max_papers:" not in script


def test_search_review_uses_server_pagination_and_persisted_selection() -> None:
    markup = Path("src/research_agent/api/frontend/index.html").read_text(
        encoding="utf-8"
    )
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    assert 'id="candidatePagination"' in markup
    assert 'id="candidatePageSize"' in markup
    assert "全选本页" in markup
    assert "取消本页" in markup
    assert "async function loadReviewPage" in script
    assert "/search-review/selection" in script
    assert 'method: "PATCH"' in script
    assert "renderCandidateCards();\n      updateReviewStats();" not in script


def test_research_chat_thread_scrolls_independently() -> None:
    styles = Path("src/research_agent/api/frontend/styles.css").read_text(
        encoding="utf-8"
    )
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    chat_styles = styles.split(".research-chat-thread {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto" in chat_styles
    assert "overflow-x: hidden" in chat_styles
    assert "justify-content: flex-start" in chat_styles
    assert "isChatNearBottom" in script
    assert "if (shouldFollowStream) scrollChatToBottom()" in script


def test_candidate_card_only_labels_simple_screening_reason() -> None:
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    assert 'reasonLabel.textContent = "筛选依据"' in script
    assert "筛选依据 / 核心内容" not in script
