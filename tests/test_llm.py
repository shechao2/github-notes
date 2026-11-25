"""M2：大模型改写与护栏（不联网，HTTP 打桩）。"""
import pytest

from app import answer, config, llm


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_completion(text):
    return FakeResponse({
        "model": "deepseek-chat",
        "choices": [{"message": {"content": text}}],
        "usage": {"total_tokens": 123},
    })


def test_grounded_accepts_numbers_from_evidence():
    evidence = [{"faq_id": "FAQ-002", "question": "S7 续航多少", "answer": "S7 后驱版 CLTC 续航 705km，四驱版 650km。"}]
    assert llm.grounded("S7 后驱版能跑 705 公里，四驱是 650 公里。", evidence) is True


def test_grounded_rejects_invented_numbers():
    evidence = [{"faq_id": "FAQ-032", "question": "保养多少钱", "answer": "小保养 480—620 元，含工时。"}]
    assert llm.grounded("小保养只要 199 元，还能再送两次。", evidence) is False


def test_build_messages_contains_evidence_and_rules():
    messages = llm.build_messages(
        "S7 续航多少",
        {"intent": "补能充电", "scene": "售后", "models": ["S7"], "emotion": "平稳"},
        [{"faq_id": "FAQ-002", "question": "S7 续航多少", "answer": "705km", "source": "官网", "owner": "张敏"}],
    )
    assert messages[0]["role"] == "system"
    assert "NEED_HANDOFF" in messages[0]["content"]
    assert "705km" in messages[1]["content"]
    assert "S7" in messages[1]["content"]


def test_compose_requires_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    with pytest.raises(RuntimeError):
        llm.compose("hi", {}, [])


def test_compose_returns_text(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _fake_completion("S7 后驱版能跑 705 公里。"))
    out = llm.compose("S7 续航多少", {"intent": "补能充电"}, [{"faq_id": "FAQ-002", "question": "q", "answer": "705km", "source": "s", "owner": "o"}])
    assert out["status"] == "ok"
    assert "705" in out["text"]
    assert out["usage"]["total_tokens"] == 123


def test_compose_detects_handoff(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _fake_completion("NEED_HANDOFF"))
    out = llm.compose("给我个底价", {}, [])
    assert out["status"] == "handoff"


def _enable_llm(monkeypatch, compose):
    monkeypatch.setattr(answer.llm, "available", lambda: True)
    monkeypatch.setattr(answer.llm, "compose", compose)


def test_decision_uses_grounded_rewrite(conn, monkeypatch):
    _enable_llm(
        monkeypatch,
        lambda text, analysis, evidence: {
            "status": "ok",
            "text": "S7 后驱版 CLTC 续航是 705 公里，四驱版 650 公里，冬天按七折估就行。",
            "model": "deepseek-chat",
            "usage": {"total_tokens": 100},
        },
    )
    decision = answer.decide(conn, "S7 续航多少")
    assert decision["status"] == "answered"
    assert decision["llm"]["used"] is True
    assert decision["answer"].startswith("S7 后驱版 CLTC 续航是 705 公里")
    assert "777" not in decision["answer"]


def test_decision_falls_back_when_numbers_invented(conn, monkeypatch):
    _enable_llm(
        monkeypatch,
        lambda text, analysis, evidence: {"status": "ok", "text": "现在订车还能便宜 8888 元。", "model": "m"},
    )
    decision = answer.decide(conn, "S7 续航多少")
    assert decision["llm"]["used"] is False
    assert "8888" not in decision["answer"]
    assert decision["evidence"][0]["faq_id"] == "FAQ-002"


def test_decision_survives_llm_error(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise TimeoutError("timeout")

    _enable_llm(monkeypatch, boom)
    decision = answer.decide(conn, "首保时间")
    assert decision["status"] == "answered"
    assert decision["llm"]["used"] is False
    assert "TimeoutError" in decision["llm"]["reason"]


def test_llm_can_escalate_to_handoff(conn, monkeypatch):
    _enable_llm(
        monkeypatch,
        lambda text, analysis, evidence: {"status": "handoff", "text": "NEED_HANDOFF", "model": "m"},
    )
    decision = answer.decide(conn, "S7 续航多少")
    assert decision["status"] == "handoff"
    assert decision["llm"].get("escalated") == "handoff"
