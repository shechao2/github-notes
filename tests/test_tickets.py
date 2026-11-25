"""工单与 SLA：建单、时限、闭环、FAQ 回写。"""
from datetime import datetime, timedelta

import pytest

from app import answer, db, kb, tickets


def _complaint_ticket(conn):
    decision = answer.decide(conn, "我要投诉门店服务态度")
    return tickets.create_ticket(
        conn, session_id=None, message_id=None, question="我要投诉门店服务态度", decision=decision
    )


def test_handoff_creates_ticket_with_sla(conn):
    ticket = _complaint_ticket(conn)
    assert ticket["priority"] == "P0"
    assert ticket["status"] == "待处理"
    created = datetime.fromisoformat(ticket["created_at"])
    first_due = datetime.fromisoformat(ticket["sla_first_due"])
    assert first_due - created == timedelta(minutes=15)
    assert ticket["sla"]["first_response"]["overdue"] is False


def test_first_response_is_stamped_on_progress(conn):
    ticket = _complaint_ticket(conn)
    updated = tickets.update_ticket(conn, ticket["id"], {"status": "处理中", "owner": "客户运营-赵倩"})
    assert updated["first_response_at"] is not None
    assert updated["status"] == "处理中"


def test_resolve_stamps_time_and_satisfaction(conn):
    ticket = _complaint_ticket(conn)
    updated = tickets.update_ticket(
        conn,
        ticket["id"],
        {"status": "已解决", "root_cause": "接待态度", "solution": "已致歉并回访", "satisfaction": 4},
    )
    assert updated["resolved_at"] is not None
    assert updated["satisfaction"] == 4
    assert updated["sla"]["resolve"]["overdue"] is False


def test_draft_requires_resolved_ticket_and_solution(conn):
    ticket = _complaint_ticket(conn)
    with pytest.raises(ValueError):
        tickets.create_faq_draft(conn, ticket["id"])

    tickets.update_ticket(conn, ticket["id"], {"status": "已解决", "solution": "按合同条款处理"})
    draft = tickets.create_faq_draft(conn, ticket["id"])
    assert draft["status"] == "待审核"
    assert draft["source"].startswith("工单回写")

    published = tickets.publish_faq(conn, draft["id"])
    assert published["status"] == "生效"


def test_overdue_is_detected(conn):
    ticket = _complaint_ticket(conn)
    past = (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE tickets SET sla_first_due = ? WHERE id = ?", (past, ticket["id"]))
    conn.commit()
    refreshed = tickets.get_ticket(conn, ticket["id"])
    assert refreshed["sla"]["first_response"]["overdue"] is True
    assert refreshed["sla"]["first_response"]["remaining_minutes"] < 0


def test_invalid_status_rejected(conn):
    ticket = _complaint_ticket(conn)
    with pytest.raises(ValueError):
        tickets.update_ticket(conn, ticket["id"], {"status": "已失联"})
