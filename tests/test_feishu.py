"""飞书字段映射与去重写入（不联网：HTTP 层用 monkeypatch 假掉）。"""
from app import feishu


SESSION = {
    "id": "sess-1",
    "customer": "周先生",
    "channel": "官网",
    "messages": [
        {"role": "user", "text": "S7 续航多少", "created_at": "2026-11-26T03:30:00+08:00"},
        {
            "role": "ai",
            "text": "S7 后驱版 CLTC 续航 705km",
            "intent": "补能充电",
            "emotion": "平稳",
            "decision": {"intent": "补能充电", "scene": "售后", "models": ["S7"], "emotion": "平稳", "priority": "P2"},
        },
    ],
    "tickets": [{"status": "已解决", "owner": "售后-李工", "priority": "P1", "sla_resolve_due": "2026-11-26T11:30:00+08:00"}],
}


def test_field_spec_matches_scheme():
    names = [field["field_name"] for field in feishu.FIELD_SPEC]
    assert len(names) == 16
    for expected in ("会话ID", "咨询意图", "问题状态", "负责人", "优先级", "SLA节点"):
        assert expected in names


def test_session_fields_mapping():
    fields = feishu.session_fields(SESSION)
    assert fields["会话ID"] == "sess-1"
    assert fields["客户昵称"] == "周先生"
    assert fields["平台"] == "官网"
    assert fields["咨询意图"] == "补能充电"
    assert fields["标签"] == ["S7"]
    assert fields["询问商品"] == "S7"
    assert fields["问题状态"] == "已解决"
    assert fields["负责人"] == "售后-李工"
    assert fields["优先级"] == "P1"
    assert fields["售价"] == 19.98
    assert isinstance(fields["时间"], int) and fields["时间"] > 0
    assert isinstance(fields["SLA节点"], int)


def test_session_without_ticket_defaults():
    session = {"id": "s2", "customer": "访客", "messages": [], "tickets": []}
    fields = feishu.session_fields(session)
    assert fields["问题状态"] == "待处理"
    assert fields["负责人"] == "未分配"
    assert fields["标签"] == ["未提及车型"]
    assert fields["售价"] is None


def test_models_union_across_messages():
    session = {
        "id": "s3", "customer": "韩女士", "messages": [
            {"role": "ai", "status": "accepted", "decision": {"models": ["L9"], "intent": "车型配置", "scene": "售前"}},
            {"role": "ai", "status": "accepted", "decision": {"models": [], "intent": "补能充电", "scene": "售前"}},
        ], "tickets": [],
    }
    fields = feishu.session_fields(session)
    assert fields["标签"] == ["L9"]
    assert fields["售价"] == 28.98
    assert fields["问题状态"] == "已解决"
    assert fields["负责人"] == "AI 自动"


def test_upsert_updates_existing_record(monkeypatch):
    calls = []

    monkeypatch.setattr(feishu, "list_records", lambda app, table: [{"record_id": "rec-1", "fields": {"会话ID": "sess-1"}}])
    monkeypatch.setattr(feishu, "_request", lambda method, path, payload=None, token=None: calls.append((method, path)) or {})

    record_id = feishu.upsert_record("app_x", "tbl_x", "sess-1", {"会话ID": "sess-1"})
    assert record_id == "rec-1"
    assert calls[0][0] == "PUT"


def test_upsert_creates_when_missing(monkeypatch):
    calls = []

    def fake_request(method, path, payload=None, token=None):
        calls.append(method)
        return {"record": {"record_id": "rec-new"}}

    monkeypatch.setattr(feishu, "list_records", lambda app, table: [])
    monkeypatch.setattr(feishu, "_request", fake_request)

    assert feishu.upsert_record("app_x", "tbl_x", "sess-9", {"会话ID": "sess-9"}) == "rec-new"
    assert calls == ["POST"]


def test_text_field_returned_as_rich_text_is_matched(monkeypatch):
    records = [{"record_id": "rec-2", "fields": {"会话ID": [{"text": "sess-1"}]}}]
    monkeypatch.setattr(feishu, "list_records", lambda app, table: records)
    monkeypatch.setattr(feishu, "_request", lambda *a, **k: {})

    assert feishu.upsert_record("app_x", "tbl_x", "sess-1", {"会话ID": "sess-1"}) == "rec-2"


def test_missing_credentials_raise(monkeypatch):
    import pytest

    monkeypatch.setattr(feishu.config, "FEISHU_APP_ID", "")
    monkeypatch.setattr(feishu.config, "FEISHU_APP_SECRET", "")
    feishu._token_cache.update({"token": "", "expire_at": 0})
    with pytest.raises(feishu.FeishuError):
        feishu.tenant_token()


def test_ticket_field_spec():
    names = [f["field_name"] for f in feishu.TICKET_FIELD_SPEC]
    assert len(names) == 16
    for expected in ("工单编号", "转人工原因", "根因分类", "最终解决方案", "用户满意度", "是否建议新增FAQ"):
        assert expected in names


def test_ticket_fields_mapping():
    ticket = {
        "no": "T-20261126-001",
        "question": "我要投诉门店服务态度",
        "intent": "投诉",
        "model": "",
        "scene": "边界",
        "faq_ids": ["FAQ-061"],
        "ai_answer": "需要人工跟进",
        "handoff_reason": "意图「投诉」必须人工接管",
        "priority": "P0",
        "owner": "客户运营-赵倩",
        "status": "已解决",
        "first_response_at": "2026-11-26T03:35:00+08:00",
        "resolved_at": "2026-11-26T04:05:00+08:00",
        "root_cause": "门店接待态度",
        "solution": "已致歉并回访",
        "satisfaction": 4,
        "suggest_faq": 1,
    }
    fields = feishu.ticket_fields(ticket)
    assert fields["工单编号"] == "T-20261126-001"
    assert fields["优先级"] == "P0"
    assert fields["处理状态"] == "已解决"
    assert fields["用户满意度"] == 4
    assert fields["是否建议新增FAQ"] is True
    assert isinstance(fields["解决时间"], int)
    assert "边界" in fields["车型与场景"]
