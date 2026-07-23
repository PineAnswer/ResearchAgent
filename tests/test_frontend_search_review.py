from pathlib import Path


def test_research_library_is_primary_navigation_with_smart_views() -> None:
    markup = Path("src/research_agent/api/frontend/index.html").read_text(
        encoding="utf-8"
    )
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    assert 'id="libraryToggle"' in markup
    assert 'id="researchLibraryToggle"' in markup
    assert markup.index('id="libraryToggle"') < markup.index('id="researchLibraryToggle"')
    assert 'id="allProjectsToggle"' not in markup
    assert "<h2>研究库</h2>" in markup
    assert 'data-project-status="active"' in markup
    assert 'id="projectSort"' in markup
    assert "elements.researchLibraryToggle.addEventListener" in script
    assert 'elements.allProjectsToggle' not in script
    assert 'id="researchRelationDialog"' in markup
    assert 'id="clearSimilarityFocus"' in markup
    assert "async function searchResearchLibrary" in script
    assert "async function loadResearchLibraryInsights" in script
    assert "function focusSimilarResearch" in script
    assert "function openResearchRelationDialog" in script
    assert '"/api/research-relations"' in script


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


def test_new_research_form_uses_compact_question_field() -> None:
    markup = Path("src/research_agent/api/frontend/index.html").read_text(
        encoding="utf-8"
    )
    styles = Path("src/research_agent/api/frontend/styles.css").read_text(
        encoding="utf-8"
    )

    assert 'id="questionInput"' in markup
    assert 'rows="3"' in markup
    question_styles = styles.split(".field-question textarea {", 1)[1].split("}", 1)[0]
    assert "height: 96px" in question_styles
    assert "min-height: 96px" in question_styles


def test_search_review_uses_server_pagination_and_persisted_selection() -> None:
    markup = Path("src/research_agent/api/frontend/index.html").read_text(
        encoding="utf-8"
    )
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    assert 'id="candidatePagination"' in markup
    assert 'id="candidatePageSize"' in markup
    assert "全选本页" in markup
    assert "取消本页" in markup
    assert 'id="selectAllCandidates"' in markup
    assert 'id="clearAllCandidates"' in markup
    assert "全选全部" in markup
    assert "取消全部" in markup
    assert "async function loadReviewPage" in script
    assert "async function setAllReviewSelection" in script
    assert "all_candidates: true" in script
    assert "/search-review/selection" in script
    assert 'method: "PATCH"' in script
    assert "renderCandidateCards();\n      updateReviewStats();" not in script


def test_search_query_rounds_use_available_width() -> None:
    styles = Path("src/research_agent/api/frontend/styles.css").read_text(
        encoding="utf-8"
    )
    script = Path("src/research_agent/api/frontend/app.js").read_text(encoding="utf-8")

    query_item_styles = styles.split(".review-query-list li {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 420px" in query_item_styles
    assert "max-width" not in query_item_styles
    assert "function stripQueryRoundPrefix" in script
    assert ".map((round) => round.map(stripQueryRoundPrefix).filter(Boolean))" in script


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
