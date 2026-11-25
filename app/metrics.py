"""运营看板指标：全部从会话、消息、工单、FAQ 四张表实时算，不引入 BI。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from . import config, tickets


def _window(days: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")


def dashboard(conn: sqlite3.Connection, days: int = 30) -> dict[str, Any]:
    since = _window(days)
    q = lambda sql, args=(): conn.execute(sql, args).fetchone()[0]  # noqa: E731

    sessions = q("SELECT COUNT(*) FROM sessions WHERE created_at >= ?", (since,))
    user_messages = q(
        "SELECT COUNT(*) FROM messages WHERE role = 'user' AND created_at >= ?", (since,)
    )
    ai_messages = q(
        "SELECT COUNT(*) FROM messages WHERE role = 'ai' AND created_at >= ?", (since,)
    )

    status_rows = conn.execute(
        "SELECT status, COUNT(*) c FROM messages WHERE role = 'ai' AND created_at >= ? GROUP BY status",
        (since,),
    ).fetchall()
    by_status = {row["status"]: row["c"] for row in status_rows}

    hit = q(
        """SELECT COUNT(*) FROM messages
           WHERE role = 'ai' AND created_at >= ? AND faq_ids != '[]' AND confidence >= ?""",
        (since, config.CONFIDENCE_THRESHOLD),
    )
    reviewed = sum(by_status.get(s, 0) for s in ("accepted", "edited", "rejected", "handed_off"))
    accepted = by_status.get("accepted", 0) + by_status.get("edited", 0)

    feedback_rows = conn.execute(
        """SELECT feedback, COUNT(*) c FROM messages
           WHERE role = 'ai' AND feedback IS NOT NULL AND created_at >= ? GROUP BY feedback""",
        (since,),
    ).fetchall()
    feedback = {row["feedback"]: row["c"] for row in feedback_rows}

    ticket_rows = conn.execute(
        "SELECT * FROM tickets WHERE created_at >= ?", (since,)
    ).fetchall()
    ticket_list = [tickets.with_sla(dict(row)) for row in ticket_rows]
    open_tickets = [t for t in ticket_list if t["status"] in {"待处理", "处理中"}]
    satisfaction = [t["satisfaction"] for t in ticket_list if t["satisfaction"] is not None]

    first_responses = []
    for t in ticket_list:
        if t["first_response_at"]:
            try:
                delta = datetime.fromisoformat(t["first_response_at"]) - datetime.fromisoformat(t["created_at"])
                first_responses.append(delta.total_seconds() / 60)
            except ValueError:
                continue

    top_intents = [
        {"intent": row["intent"], "count": row["c"]}
        for row in conn.execute(
            """SELECT intent, COUNT(*) c FROM messages
               WHERE role = 'user' AND created_at >= ? GROUP BY intent ORDER BY c DESC LIMIT 10""",
            (since,),
        ).fetchall()
    ]
    top_faq = [
        {"faq_id": row["id"], "question": row["question"], "hits": row["hits"]}
        for row in conn.execute(
            "SELECT id, question, hits FROM faq ORDER BY hits DESC LIMIT 10"
        ).fetchall()
        if row["hits"] > 0
    ]
    unmatched = [
        {"question": row["q"], "count": row["c"]}
        for row in conn.execute(
            """SELECT json_extract(decision, '$.text') AS q, COUNT(*) c FROM messages
               WHERE role = 'ai' AND status = 'no_hit' AND created_at >= ?
               GROUP BY q ORDER BY c DESC LIMIT 10""",
            (since,),
        ).fetchall()
    ]

    handoff_by_intent = [
        {"intent": row["intent"], "count": row["c"]}
        for row in conn.execute(
            """SELECT intent, COUNT(*) c FROM tickets
               WHERE created_at >= ? GROUP BY intent HAVING c >= 2 ORDER BY c DESC""",
            (since,),
        ).fetchall()
    ]

    faq_new = q(
        "SELECT COUNT(*) FROM faq WHERE datetime(created_at) >= datetime(?)", (since,)
    )
    faq_updated = q(
        "SELECT COUNT(*) FROM faq WHERE date(updated_at) >= date(?) AND status = '生效'", (since,)
    )
    faq_pending = q("SELECT COUNT(*) FROM faq WHERE status = '待审核'")
    faq_total = q("SELECT COUNT(*) FROM faq WHERE status = '生效'")

    overdue_first = sum(1 for t in ticket_list if t["sla"]["first_response"]["overdue"])
    overdue_resolve = sum(1 for t in ticket_list if t["sla"]["resolve"]["overdue"])

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 3) if denominator else 0.0

    return {
        "range_days": days,
        "sessions": sessions,
        "messages": user_messages,
        "ai_messages": ai_messages,
        "faq_hit_rate": rate(hit, ai_messages),
        "auto_resolve_rate": rate(accepted, reviewed),
        "handoff_rate": rate(by_status.get("handed_off", 0), ai_messages),
        "no_hit_rate": rate(by_status.get("no_hit", 0), ai_messages),
        "avg_satisfaction": round(sum(satisfaction) / len(satisfaction), 2) if satisfaction else None,
        "avg_first_response_minutes": round(sum(first_responses) / len(first_responses), 1)
        if first_responses
        else None,
        "status_breakdown": by_status,
        "feedback_breakdown": feedback,
        "tickets": {
            "total": len(ticket_list),
            "open": len(open_tickets),
            "overdue_first_response": overdue_first,
            "overdue_resolve": overdue_resolve,
        },
        "top_intents": top_intents,
        "top_faq": top_faq,
        "unmatched_questions": unmatched,
        "repeat_handoff": handoff_by_intent,
        "faq": {"total": faq_total, "new": faq_new, "updated": faq_updated, "pending_review": faq_pending},
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "thresholds": {
            "confidence": config.CONFIDENCE_THRESHOLD,
            "clarify": config.CLARIFY_THRESHOLD,
        },
    }
