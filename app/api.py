"""REST API：FastAPI 路由层，只做参数校验与调用服务层。"""
from __future__ import annotations

import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import config, db, kb, metrics, service, tickets


def get_conn(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


router = APIRouter(prefix="/api")


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    session_id: str | None = None
    customer: str | None = None
    channel: str | None = None


class ReviewIn(BaseModel):
    action: Literal["accepted", "edited", "rejected", "handed_off"]
    text: str | None = None
    reviewer: str = "客服-演示账号"


class FeedbackIn(BaseModel):
    result: Literal["solved", "unsolved", "wrong"]


class TicketPatch(BaseModel):
    status: Literal["待处理", "处理中", "已解决", "已关闭"] | None = None
    owner: str | None = None
    root_cause: str | None = None
    solution: str | None = None
    satisfaction: int | None = Field(default=None, ge=1, le=5)
    suggest_faq: bool | None = None
    priority: Literal["P0", "P1", "P2"] | None = None


class FaqIn(BaseModel):
    question: str
    answer: str
    category: str = "其他"
    variants: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=lambda: [config.ALL_MODELS])
    scene: str = ""
    source: str = "人工录入"
    owner: str = "待指派"
    tags: list[str] = Field(default_factory=list)
    priority: Literal["P0", "P1", "P2"] = "P2"
    status: Literal["生效", "待审核", "停用"] = "待审核"
    id: str | None = None


class FaqPatch(BaseModel):
    question: str | None = None
    answer: str | None = None
    category: str | None = None
    variants: list[str] | None = None
    models: list[str] | None = None
    scene: str | None = None
    source: str | None = None
    owner: str | None = None
    tags: list[str] | None = None
    priority: Literal["P0", "P1", "P2"] | None = None
    status: Literal["生效", "待审核", "停用"] | None = None


@router.get("/health")
def health(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    return {
        "ok": True,
        "faq": conn.execute("SELECT COUNT(*) FROM faq").fetchone()[0],
        "sessions": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "tickets": conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0],
    }


@router.get("/config")
def get_config() -> dict[str, Any]:
    return {
        "brand": config.BRAND,
        "models": config.MODELS,
        "confidence_threshold": config.CONFIDENCE_THRESHOLD,
        "clarify_threshold": config.CLARIFY_THRESHOLD,
        "sla_rules": config.SLA_RULES,
        "llm_enabled": config.LLM_ENABLED,
    }


@router.post("/chat")
def chat(payload: ChatIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    if payload.session_id:
        session = service.get_session(conn, payload.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        session_id = payload.session_id
    else:
        session_id = service.create_session(
            conn, customer=payload.customer or "", channel=payload.channel or "官网"
        )["id"]
    return service.add_turn(conn, session_id, payload.text)


@router.get("/sessions")
def sessions(limit: int = Query(50, ge=1, le=200), conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": service.list_sessions(conn, limit=limit)}


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    session = service.get_session(conn, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@router.post("/messages/{message_id}/review")
def review(message_id: str, payload: ReviewIn, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return service.review_message(conn, message_id, payload.action, payload.text, payload.reviewer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: str, payload: FeedbackIn, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return service.feedback(conn, message_id, payload.result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tickets")
def list_tickets(status: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": tickets.list_tickets(conn, status=status)}


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    ticket = tickets.get_ticket(conn, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket


@router.patch("/tickets/{ticket_id}")
def patch_ticket(ticket_id: str, payload: TicketPatch, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        ticket = tickets.update_ticket(conn, ticket_id, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket


@router.post("/tickets/{ticket_id}/faq-draft")
def ticket_faq_draft(ticket_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return tickets.create_faq_draft(conn, ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/faq")
def list_faq(
    q: str | None = None,
    status: str | None = None,
    category: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict[str, Any]:
    if q:
        hits = kb.search(conn, q, top_k=50)
        return {"items": hits, "mode": "search"}
    sql = "SELECT * FROM faq WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if category:
        sql += " AND category = ?"
        args.append(category)
    sql += " ORDER BY id"
    rows = [db.row_to_dict(row, kb.JSON_FIELDS) for row in conn.execute(sql, args).fetchall()]
    return {"items": rows, "mode": "list"}


@router.post("/faq")
def create_faq(payload: FaqIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    entry = payload.model_dump()
    entry["id"] = entry.get("id") or kb.next_faq_id(conn)
    return kb.upsert_entry(conn, entry)


@router.patch("/faq/{faq_id}")
def patch_faq(faq_id: str, payload: FaqPatch, conn: sqlite3.Connection = Depends(get_conn)):
    entry = kb.get_entry(conn, faq_id)
    if not entry:
        raise HTTPException(status_code=404, detail="FAQ 不存在")
    entry.update({k: v for k, v in payload.model_dump(exclude_none=True).items()})
    return kb.upsert_entry(conn, entry)


@router.post("/faq/{faq_id}/publish")
def publish(faq_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    entry = tickets.publish_faq(conn, faq_id)
    if not entry:
        raise HTTPException(status_code=404, detail="FAQ 不存在")
    return entry


@router.get("/dashboard")
def dashboard(days: int = Query(30, ge=1, le=365), conn: sqlite3.Connection = Depends(get_conn)):
    return metrics.dashboard(conn, days=days)


@router.get("/feishu/status")
def feishu_status() -> dict[str, Any]:
    from . import feishu

    return {
        "configured": bool(config.FEISHU_APP_ID and config.FEISHU_APP_SECRET),
        "base_ready": bool(config.FEISHU_BITABLE_TOKEN and config.FEISHU_TABLE_ID),
        "fields": [field["field_name"] for field in feishu.FIELD_SPEC],
    }


@router.post("/feishu/sync")
def feishu_sync(session_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    from . import feishu

    try:
        if session_id:
            session = service.get_session(conn, session_id)
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
            return feishu.sync_session(session)
        rows = conn.execute("SELECT id FROM sessions ORDER BY updated_at DESC").fetchall()
        synced = 0
        for row in rows:
            session = service.get_session(conn, row["id"])
            if session:
                feishu.sync_session(session)
                synced += 1
        return {"synced": synced}
    except feishu.FeishuError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/demo/seed")
def demo_seed(reset: bool = False, conn: sqlite3.Connection = Depends(get_conn)):
    from . import seed as seed_module

    return seed_module.seed_all(conn, reset=reset)


@router.post("/demo/reset")
def demo_reset(conn: sqlite3.Connection = Depends(get_conn)):
    from . import seed as seed_module

    return seed_module.reset_db(conn)
