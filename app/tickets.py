"""工单与 SLA：转人工建单、SLA 时限、处理闭环、FAQ 回写草稿。"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from . import config, kb
from .db import dumps, log_event, now_iso, row_to_dict

JSON_FIELDS = ("faq_ids",)
OPEN_STATUS = {"待处理", "处理中"}
VALID_STATUS = {"待处理", "处理中", "已解决", "已关闭"}


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def next_no(conn: sqlite3.Connection) -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    count = conn.execute("SELECT COUNT(*) FROM tickets WHERE no LIKE ?", (f"T-{stamp}-%",)).fetchone()[0]
    return f"T-{stamp}-{count + 1:03d}"


def create_ticket(
    conn: sqlite3.Connection,
    *,
    session_id: str | None,
    message_id: str | None,
    question: str,
    decision: dict[str, Any],
    owner: str = "",
) -> dict[str, Any]:
    priority = decision.get("priority", "P2")
    first_min, resolve_min = config.SLA_RULES.get(priority, config.SLA_RULES["P2"])
    created = datetime.now().astimezone()
    ticket = {
        "id": uuid.uuid4().hex[:12],
        "no": next_no(conn),
        "session_id": session_id,
        "message_id": message_id,
        "question": question,
        "intent": decision.get("intent", ""),
        "model": ",".join(decision.get("models") or []),
        "scene": decision.get("scene", ""),
        "faq_ids": dumps([e["faq_id"] for e in decision.get("evidence", [])]),
        "ai_answer": decision.get("answer", ""),
        "handoff_reason": decision.get("handoff_reason", ""),
        "priority": priority,
        "owner": owner or "未分配",
        "status": "待处理",
        "created_at": created.isoformat(timespec="seconds"),
        "updated_at": created.isoformat(timespec="seconds"),
        "sla_first_due": (created + timedelta(minutes=first_min)).isoformat(timespec="seconds"),
        "sla_resolve_due": (created + timedelta(minutes=resolve_min)).isoformat(timespec="seconds"),
    }
    conn.execute(
        """INSERT INTO tickets(id, no, session_id, message_id, question, intent, model, scene,
           faq_ids, ai_answer, handoff_reason, priority, owner, status, created_at, updated_at,
           sla_first_due, sla_resolve_due)
           VALUES(:id, :no, :session_id, :message_id, :question, :intent, :model, :scene,
           :faq_ids, :ai_answer, :handoff_reason, :priority, :owner, :status, :created_at,
           :updated_at, :sla_first_due, :sla_resolve_due)""",
        ticket,
    )
    log_event(conn, "ticket.created", ticket["no"], {"priority": priority, "intent": ticket["intent"]})
    conn.commit()
    return get_ticket(conn, ticket["id"])  # type: ignore[return-value]


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not row:
        return None
    return with_sla(row_to_dict(row, JSON_FIELDS))


def list_tickets(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM tickets"
    args: tuple[Any, ...] = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    sql += " ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END, created_at DESC"
    return [with_sla(row_to_dict(row, JSON_FIELDS)) for row in conn.execute(sql, args).fetchall()]


def with_sla(ticket: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now().astimezone()
    first_due = _parse(ticket.get("sla_first_due"))
    resolve_due = _parse(ticket.get("sla_resolve_due"))
    closed = ticket.get("status") in {"已解决", "已关闭"}

    def state(due: datetime | None, done: str | None) -> dict[str, Any]:
        if due is None:
            return {"due": None, "remaining_minutes": None, "overdue": False}
        finished = _parse(done)
        if finished:
            return {"due": due.isoformat(timespec="seconds"),
                    "remaining_minutes": int((finished - due).total_seconds() // 60), "overdue": False}
        remaining = int((due - now).total_seconds() // 60)
        return {"due": due.isoformat(timespec="seconds"), "remaining_minutes": remaining,
                "overdue": remaining < 0 and not closed}

    ticket["sla"] = {
        "first_response": state(first_due, ticket.get("first_response_at")),
        "resolve": state(resolve_due, ticket.get("resolved_at")),
    }
    return ticket


def update_ticket(conn: sqlite3.Connection, ticket_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    ticket = get_ticket(conn, ticket_id)
    if not ticket:
        return None
    allowed = {
        "status", "owner", "root_cause", "solution", "satisfaction", "suggest_faq", "priority",
    }
    updates = {k: v for k, v in patch.items() if k in allowed and v is not None}
    if "status" in updates and updates["status"] not in VALID_STATUS:
        raise ValueError(f"非法工单状态：{updates['status']}")

    now = now_iso()
    updates["updated_at"] = now
    if updates.get("status") == "处理中" and not ticket.get("first_response_at"):
        updates["first_response_at"] = now
    if updates.get("status") == "已解决" and not ticket.get("resolved_at"):
        updates["resolved_at"] = now
        if not ticket.get("first_response_at"):
            updates["first_response_at"] = now

    if "priority" in updates and updates["priority"] != ticket["priority"]:
        first_min, resolve_min = config.SLA_RULES.get(updates["priority"], config.SLA_RULES["P2"])
        created = _parse(ticket["created_at"]) or datetime.now().astimezone()
        updates["sla_first_due"] = (created + timedelta(minutes=first_min)).isoformat(timespec="seconds")
        updates["sla_resolve_due"] = (created + timedelta(minutes=resolve_min)).isoformat(timespec="seconds")

    columns = ", ".join(f"{key} = :{key}" for key in updates)
    conn.execute(f"UPDATE tickets SET {columns} WHERE id = :id", {**updates, "id": ticket_id})
    log_event(conn, "ticket.updated", ticket["no"], {k: v for k, v in updates.items() if k != "updated_at"})
    conn.commit()
    return get_ticket(conn, ticket_id)


def create_faq_draft(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any]:
    """工单解决后把标准答案回写成 FAQ 草稿（待审核），审核通过才生效。"""
    ticket = get_ticket(conn, ticket_id)
    if not ticket:
        raise ValueError("工单不存在")
    if ticket["status"] not in {"已解决", "已关闭"}:
        raise ValueError("工单未解决，不能回写 FAQ")
    if not ticket.get("solution"):
        raise ValueError("请先填写最终解决方案")

    draft = {
        "id": kb.next_faq_id(conn),
        "category": "回写" if ticket["intent"] in {"其他", ""} else ticket["intent"],
        "question": ticket["question"],
        "variants": [],
        "models": [m for m in (ticket.get("model") or "").split(",") if m] or [config.ALL_MODELS],
        "scene": ticket.get("scene", "工单回写"),
        "answer": ticket["solution"],
        "source": f"工单回写 {ticket['no']}",
        "owner": ticket.get("owner") or "待指派",
        "updated_at": now_iso()[:10],
        "status": "待审核",
        "tags": ["工单回写", ticket.get("intent", "")],
        "priority": "P2",
    }
    kb.upsert_entry(conn, draft)
    update_ticket(conn, ticket_id, {"suggest_faq": 1})
    log_event(conn, "faq.draft", draft["id"], {"ticket": ticket["no"]})
    return kb.get_entry(conn, draft["id"]) or draft


def publish_faq(conn: sqlite3.Connection, faq_id: str) -> dict[str, Any] | None:
    entry = kb.get_entry(conn, faq_id)
    if not entry:
        return None
    entry["status"] = "生效"
    entry["updated_at"] = now_iso()[:10]
    conn.execute("UPDATE faq SET status = ?, updated_at = ? WHERE id = ?",
                 ("生效", entry["updated_at"], faq_id))
    kb.rebuild_index(conn)
    log_event(conn, "faq.published", faq_id, {})
    conn.commit()
    return kb.get_entry(conn, faq_id)
