"""REST API 冒烟：聊天 -> 审核 -> 反馈 -> 工单 -> 看板 -> 知识库。"""


def test_health_and_config(client):
    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert health["faq"] >= 60

    conf = client.get("/api/config").json()
    assert conf["brand"] == "星驰汽车"
    assert 0 < conf["confidence_threshold"] < 1


def test_chat_review_feedback_flow(client):
    chat = client.post("/api/chat", json={"text": "S7 续航多少", "customer": "周先生"}).json()
    assert chat["decision"]["status"] == "answered"
    message_id = chat["ai_message"]["id"]

    reviewed = client.post(f"/api/messages/{message_id}/review", json={"action": "accepted"})
    assert reviewed.status_code == 200
    assert reviewed.json()["message"]["status"] == "accepted"

    fed = client.post(f"/api/messages/{message_id}/feedback", json={"result": "solved"})
    assert fed.status_code == 200

    detail = client.get(f"/api/sessions/{chat['session_id']}").json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][1]["feedback"] == "solved"


def test_reject_creates_ticket(client):
    chat = client.post("/api/chat", json={"text": "你们几点下班"}).json()
    message_id = chat["ai_message"]["id"]
    out = client.post(f"/api/messages/{message_id}/review", json={"action": "rejected"}).json()
    assert out["ticket"] is not None
    assert out["ticket"]["priority"] in {"P0", "P1", "P2"}


def test_ticket_patch_and_validation(client):
    client.post("/api/chat", json={"text": "我要投诉门店服务态度"})
    tickets = client.get("/api/tickets").json()["items"]
    assert tickets
    ticket = tickets[0]

    patched = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"status": "已解决", "solution": "已回访客户", "satisfaction": 5},
    ).json()
    assert patched["status"] == "已解决"
    assert patched["satisfaction"] == 5

    bad = client.patch(f"/api/tickets/{ticket['id']}", json={"status": "不存在的状态"})
    assert bad.status_code == 422


def test_faq_requires_publish_before_search(client):
    created = client.post(
        "/api/faq",
        json={
            "question": "试驾车能上高速吗",
            "answer": "试驾车须由顾问陪同并按指定路线行驶。",
            "category": "售前",
            "status": "待审核",
        },
    ).json()
    assert created["status"] == "待审核"

    before = client.get("/api/faq", params={"q": "试驾车能上高速吗"}).json()["items"]
    assert all(item["id"] != created["id"] for item in before)

    client.post(f"/api/faq/{created['id']}/publish")
    after = client.get("/api/faq", params={"q": "试驾车能上高速吗"}).json()["items"]
    assert after[0]["id"] == created["id"]
    assert after[0]["status"] == "生效"


def test_dashboard_keys(client):
    client.post("/api/chat", json={"text": "首保时间"})
    dash = client.get("/api/dashboard").json()
    for key in ("sessions", "auto_resolve_rate", "handoff_rate", "faq_hit_rate",
                "top_intents", "unmatched_questions", "tickets", "faq"):
        assert key in dash


def test_unknown_session_returns_404(client):
    assert client.get("/api/sessions/not-exist").status_code == 404
    assert client.post("/api/chat", json={"text": "你好", "session_id": "not-exist"}).status_code == 404


def test_demo_seed_endpoint(client):
    out = client.post("/api/demo/seed?reset=true").json()
    assert out["ok"] is True
    assert out["sessions"] == 14
    assert client.get("/api/sessions").json()["items"]
