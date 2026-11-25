"""答复决策：直接回答 / 追问 / 不猜 / 转人工。"""
import pytest

from app import answer, config


def test_answer_carries_source(conn):
    decision = answer.decide(conn, "S7 续航多少")
    assert decision["status"] == "answered"
    assert decision["evidence"][0]["faq_id"] == "FAQ-002"
    assert "705" in decision["answer"]
    assert "官网参数页" in decision["answer"]


def test_low_confidence_asks_instead_of_guessing(conn):
    decision = answer.decide(conn, "多少钱")
    assert decision["status"] == "clarify"
    assert decision["clarify"]
    assert "S7" in decision["answer"] or "车型" in decision["answer"]


def test_no_hit_never_fabricates(conn):
    decision = answer.decide(conn, "你们公司股票代码是多少")
    assert decision["status"] == "no_hit"
    assert "没有可靠答案" in decision["answer"] or "转人工" in decision["answer"]
    assert all(e["confidence"] < config.CONFIDENCE_THRESHOLD for e in decision["evidence"])


def test_complaint_must_handoff_with_p0(conn):
    decision = answer.decide(conn, "我要投诉门店服务态度")
    assert decision["status"] == "handoff"
    assert decision["priority"] == "P0"
    assert "投诉" in decision["handoff_reason"]


def test_price_commitment_must_handoff(conn):
    decision = answer.decide(conn, "给我个底价，能再便宜点吗")
    assert decision["status"] == "handoff"
    assert decision["intent"] in {"价格承诺", "价格咨询"}


def test_safety_issue_is_handoff_even_with_evidence(conn):
    decision = answer.decide(conn, "刹车失灵了")
    assert decision["status"] == "handoff"
    assert decision["risk"]["level"] == "high"


def test_answered_increments_faq_hits(conn):
    before = conn.execute("SELECT hits FROM faq WHERE id = 'FAQ-029'").fetchone()[0]
    answer.decide(conn, "首保时间")
    after = conn.execute("SELECT hits FROM faq WHERE id = 'FAQ-029'").fetchone()[0]
    assert after == before + 1


def test_thresholds_are_configurable(conn, monkeypatch):
    monkeypatch.setattr(config, "CONFIDENCE_THRESHOLD", 0.99)
    decision = answer.decide(conn, "S7 续航多少")
    assert decision["status"] in {"clarify", "handoff", "no_hit"}
    assert decision["status"] != "answered"
