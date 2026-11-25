"""运营看板：指标口径与演示数据一致性。"""
from app import metrics, seed


def test_dashboard_after_demo_seed(conn):
    result = seed.seed_all(conn, reset=True)
    assert result["sessions"] == 14

    dash = metrics.dashboard(conn)
    assert dash["sessions"] == 14
    assert dash["messages"] == 22
    assert 0 <= dash["auto_resolve_rate"] <= 1
    assert 0 <= dash["handoff_rate"] <= 1
    assert 0 <= dash["faq_hit_rate"] <= 1
    assert dash["avg_satisfaction"] is not None
    assert dash["tickets"]["total"] >= 5
    assert dash["faq"]["pending_review"] >= 1
    assert dash["generated_at"]


def test_unmatched_and_repeat_handoff_are_reported(conn):
    seed.seed_all(conn, reset=True)
    dash = metrics.dashboard(conn)
    unmatched_texts = [item["question"] for item in dash["unmatched_questions"]]
    assert any("股票代码" in (text or "") for text in unmatched_texts)


def test_empty_dashboard_does_not_crash(conn):
    dash = metrics.dashboard(conn)
    assert dash["sessions"] == 0
    assert dash["auto_resolve_rate"] == 0
    assert dash["avg_satisfaction"] is None
    assert dash["avg_first_response_minutes"] is None


def test_rate_denominators_are_guarded(conn):
    """没有任何 AI 建议时，比率不能除零。"""
    dash = metrics.dashboard(conn, days=1)
    assert dash["faq_hit_rate"] == 0.0
    assert dash["handoff_rate"] == 0.0
