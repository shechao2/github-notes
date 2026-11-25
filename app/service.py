"""会话服务：建会话、发消息（含 AI 决策与建单）、审核、用户反馈。

API 层与演示数据脚本共用这一层，保证「演示数据」和「真实操作」走同一条链路。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from . import answer, config, nlu, tickets
from .db import dumps, log_event, now_iso, row_to_dict

STATUS_MAP = {
    "answered": "pending",
    "clarify": "clarify",
    "handoff": "handed_off",
    "no_hit": "no_hit",
}

JSON_FIELDS = ("faq_ids", "decision")


def create_session(conn: sqlite3.Connection, customer: str = "", channel: str = "官网") -> dict[str, Any]:
    session_id = uuid.uuid4().hex[:12]
    now = now_iso()
    customer = customer or f"访客{conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] + 1}"
    conn.execute(
        "INSERT INTO sessions(id, customer, channel, model, scene, status, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (session_id, customer, channel, "", "", "open", now, now),
    )
    log_event(conn, "session.created", session_id, {"customer": customer})
    conn.commit()
    return get_session(conn, session_id)  # type: ignore[return-value]


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    session = dict(row)
    messages = [
        row_to_dict(m, JSON_FIELDS)
        for m in conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid", (session_id,)
        ).fetchall()
    ]
    session["messages"] = messages
    ticket_rows = conn.execute(
        "SELECT * FROM tickets WHERE session_id = ? ORDER BY created_at DESC", (session_id,)
    ).fetchall()
    session["tickets"] = [tickets.with_sla(row_to_dict(t)) for t in ticket_rows]
    return session


def list_sessions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for row in rows:
        session = dict(row)
        last = conn.execute(
            "SELECT text FROM messages WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        session["last_message"] = last["text"] if last else ""
        session["message_count"] = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (row["id"],)
        ).fetchone()[0]
        result.append(session)
    return result


def add_turn(conn: sqlite3.Connection, session_id: str, text: str) -> dict[str, Any]:
    session = get_session(conn, session_id)
    if not session:
        raise ValueError("会话不存在")

    decision = answer.decide(conn, text)
    now = now_iso()
    user_id = uuid.uuid4().hex[:12]
    ai_id = uuid.uuid4().hex[:12]

    conn.execute(
        """INSERT INTO messages(id, session_id, role, text, intent, scene, emotion, confidence,
           status, faq_ids, decision, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, session_id, "user", text, decision["intent"], decision["scene"],
         decision["emotion"], 0, "ok", "[]", "{}", now),
    )
    ai_status = STATUS_MAP[decision["status"]]
    conn.execute(
        """INSERT INTO messages(id, session_id, role, text, intent, scene, emotion, confidence,
           status, faq_ids, decision, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ai_id, session_id, "ai", decision["answer"], decision["intent"], decision["scene"],
            decision["emotion"], decision["confidence"], ai_status,
            dumps([e["faq_id"] for e in decision["evidence"]]), dumps(decision), now,
        ),
    )
    conn.execute(
        "UPDATE sessions SET model = COALESCE(NULLIF(?, ''), model), scene = ?, updated_at = ? WHERE id = ?",
        (",".join(decision["models"]), decision["scene"], now, session_id),
    )

    ticket = None
    if ai_status in {"handed_off", "no_hit"}:
        ticket = tickets.create_ticket(
            conn, session_id=session_id, message_id=ai_id, question=text, decision=decision
        )
    conn.commit()
    return {
        "session_id": session_id,
        "user_message": row_to_dict(
            conn.execute("SELECT * FROM messages WHERE id = ?", (user_id,)).fetchone(), JSON_FIELDS
        ),
        "ai_message": row_to_dict(
            conn.execute("SELECT * FROM messages WHERE id = ?", (ai_id,)).fetchone(), JSON_FIELDS
        ),
        "decision": decision,
        "ticket": ticket,
    }


def review_message(
    conn: sqlite3.Connection,
    message_id: str,
    action: str,
    text: str | None = None,
    reviewer: str = "客服",
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if not row:
        raise ValueError("消息不存在")
    if row["role"] != "ai":
        raise ValueError("只能审核 AI 消息")
    if action not in {"accepted", "edited", "rejected", "handed_off"}:
        raise ValueError("非法审核动作")

    now = now_iso()
    decision = json.loads(row["decision"] or "{}")
    new_status = action
    if action == "edited":
        if not (text or "").strip():
            raise ValueError("修改后发送需要提供文本")
        decision["final_answer"] = text.strip()
        conn.execute("UPDATE messages SET text = ? WHERE id = ?", (text.strip(), message_id))
    elif action == "accepted":
        decision["final_answer"] = row["text"]

    conn.execute(
        "UPDATE messages SET status = ?, reviewed_at = ?, reviewer = ?, decision = ? WHERE id = ?",
        (new_status, now, reviewer, dumps(decision), message_id),
    )

    ticket = None
    if action in {"rejected", "handed_off"}:
        exists = conn.execute("SELECT id FROM tickets WHERE message_id = ?", (message_id,)).fetchone()
        if not exists:
            handoff_decision = dict(decision)
            handoff_decision["handoff_reason"] = (
                "客服驳回 AI 答案，转为人工处理" if action == "rejected" else "客服主动转人工"
            )
            handoff_decision.setdefault("priority", "P1")
            ticket = tickets.create_ticket(
                conn,
                session_id=row["session_id"],
                message_id=message_id,
                question=decision.get("text", row["text"]),
                decision=handoff_decision,
            )
        else:
            ticket = tickets.get_ticket(conn, exists["id"])
    log_event(conn, "message.reviewed", message_id, {"action": action, "reviewer": reviewer})
    conn.commit()
    return {
        "message": row_to_dict(
            conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone(), JSON_FIELDS
        ),
        "ticket": ticket,
    }


def feedback(conn: sqlite3.Connection, message_id: str, result: str) -> dict[str, Any]:
    if result not in {"solved", "unsolved", "wrong"}:
        raise ValueError("反馈只能是 solved / unsolved / wrong")
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if not row:
        raise ValueError("消息不存在")
    now = now_iso()
    conn.execute("UPDATE messages SET feedback = ?, feedback_at = ? WHERE id = ?", (result, now, message_id))

    ticket = None
    if result in {"unsolved", "wrong"}:
        exists = conn.execute("SELECT id FROM tickets WHERE message_id = ?", (message_id,)).fetchone()
        if not exists:
            decision = json.loads(row["decision"] or "{}")
            decision["handoff_reason"] = "用户反馈未解决" if result == "unsolved" else "用户反馈答案错误"
            ticket = tickets.create_ticket(
                conn,
                session_id=row["session_id"],
                message_id=message_id,
                question=decision.get("text", row["text"]),
                decision=decision,
            )
        else:
            ticket = tickets.get_ticket(conn, exists["id"])
    log_event(conn, "message.feedback", message_id, {"result": result})
    conn.commit()
    return {"ok": True, "feedback": result, "ticket": ticket}
