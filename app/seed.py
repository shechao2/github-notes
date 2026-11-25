"""演示数据：真实走 service 层（决策 -> 审核 -> 工单 -> 回写），保证看板数字可解释。

用法：
    python -m app.seed              # 重建演示数据
    POST /api/demo/seed?reset=true  # 同上（接口版）
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import config, db, kb, service, tickets
from .db import dumps, now_iso

TRUTH = "演示数据：人物与订单均为虚构，FAQ 内容来自演示知识库。"

SCENARIOS: list[dict] = [
    {"customer": "周先生", "channel": "官网", "turns": [
        {"text": "S7 续航多少", "review": "accepted", "feedback": "solved"},
        {"text": "冬天续航会掉多少", "review": "accepted", "feedback": "solved"},
    ]},
    {"customer": "李女士", "channel": "App", "turns": [
        {"text": "X3 首付最低多少", "review": "accepted", "feedback": "solved"},
        {"text": "月供多少", "review": "accepted", "feedback": "solved"},
    ]},
    {"customer": "王先生", "channel": "门店", "turns": [
        {"text": "想预约周末试驾", "review": "accepted", "feedback": "solved"},
        {"text": "可以上门试驾吗", "review": "edited", "reply": "可以，门店 20 公里内免费上门，您把小区地址发我，我这边帮您约周六上午。", "feedback": "solved"},
    ]},
    {"customer": "张女士", "channel": "400", "turns": [
        {"text": "车机卡顿重启怎么弄", "review": "accepted", "feedback": "solved"},
        {"text": "OTA 升级失败了", "review": "accepted", "feedback": "solved"},
    ]},
    {"customer": "陈先生", "channel": "官网", "turns": [
        {"text": "我要投诉门店服务态度", "review": "handed_off"},
    ]},
    {"customer": "刘先生", "channel": "App", "turns": [
        {"text": "定金能退吗", "review": "handed_off"},
    ]},
    {"customer": "赵女士", "channel": "门店", "turns": [
        {"text": "车机卡顿", "review": "accepted", "feedback": "solved"},
        {"text": "空调不制冷", "review": "accepted", "feedback": "solved"},
    ]},
    {"customer": "孙先生", "channel": "400", "turns": [
        {"text": "你们公司股票代码是多少", "review": None, "feedback": "unsolved"},
    ]},
    {"customer": "吴女士", "channel": "官网", "turns": [
        {"text": "多少钱", "review": None},
        {"text": "S7 多少钱", "review": "accepted", "feedback": "solved"},
    ]},
    {"customer": "郑先生", "channel": "App", "turns": [
        {"text": "刹车有异响正常吗", "review": "accepted", "feedback": "wrong"},
    ]},
    {"customer": "冯女士", "channel": "官网", "turns": [
        {"text": "首保时间", "review": "accepted", "feedback": "solved"},
        {"text": "保养多少钱", "review": "edited", "reply": "小保养 480—620 元含工时，您这台 S7 首保免费，之后每次按 12 个月或 1 万公里做就行。", "feedback": "solved"},
    ]},
    {"customer": "何先生", "channel": "400", "turns": [
        {"text": "充电桩亮红灯不工作", "review": "accepted", "feedback": "solved"},
    ]},
    {"customer": "许先生", "channel": "官网", "turns": [
        {"text": "我想退车", "review": "handed_off"},
    ]},
    {"customer": "韩女士", "channel": "App", "turns": [
        {"text": "L9 是增程还是纯电", "review": "accepted", "feedback": "solved"},
        {"text": "L9 综合续航多少", "review": "accepted", "feedback": "solved"},
    ]},
]

RESOLUTIONS: dict[str, dict] = {
    "投诉": {"root_cause": "门店接待态度", "solution": "已致歉并安排店长 2 小时内回访，客户接受。", "satisfaction": 3},
    "退订退款": {"root_cause": "客户对交付周期不满要求退订", "solution": "核对合同为锁单前订单，已按流程全额退还定金。", "satisfaction": 4},
    "价格承诺": {"root_cause": "客户要求报底价", "solution": "销售顾问按当期活动口径回复，未作价格承诺。", "satisfaction": 4},
    "其他": {"root_cause": "知识库未覆盖（公司信息类）", "solution": "公司未上市，暂无股票代码；对外统一回复「暂无相关信息」。", "satisfaction": 4},
    "故障维修": {"root_cause": "刹车异响（刹车片磨损）", "solution": "到店检查为刹车片磨到极限，更换前轮刹车片后复测正常。", "satisfaction": 5},
}

DRAFT_ONLY = {"故障维修"}


def reset_db(conn: sqlite3.Connection) -> dict:
    for table in ("messages", "tickets", "sessions", "faq_fts", "faq", "events"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    return {"ok": True, "cleared": True}


def seed_all(conn: sqlite3.Connection, reset: bool = True) -> dict:
    if reset:
        reset_db(conn)
    faq_count = kb.seed_from_file(conn, config.FAQ_FILE)

    sessions = 0
    messages = 0
    created_tickets: list[str] = []
    for scenario in SCENARIOS:
        session = service.create_session(conn, scenario["customer"], scenario["channel"])
        sessions += 1
        for turn in scenario["turns"]:
            out = service.add_turn(conn, session["id"], turn["text"])
            messages += 2
            if out["ticket"]:
                created_tickets.append(out["ticket"]["id"])
            ai_id = out["ai_message"]["id"]
            review = turn.get("review")
            if review:
                result = service.review_message(
                    conn, ai_id, review, text=turn.get("reply"), reviewer="客服-演示账号"
                )
                if result["ticket"]:
                    created_tickets.append(result["ticket"]["id"])
            if turn.get("feedback"):
                fb = service.feedback(conn, ai_id, turn["feedback"])
                if fb["ticket"]:
                    created_tickets.append(fb["ticket"]["id"])

    created_tickets = list(dict.fromkeys(created_tickets))

    # 工单处理：按意图套用解决方案，留 2 单在处理中，1 单超时做演示
    resolved = 0
    for index, ticket_id in enumerate(created_tickets):
        ticket = tickets.get_ticket(conn, ticket_id)
        if not ticket:
            continue
        if index >= len(created_tickets) - 1:
            tickets.update_ticket(conn, ticket_id, {"owner": "售后-李工", "status": "处理中"})
            continue
        plan = RESOLUTIONS.get(ticket["intent"], RESOLUTIONS["其他"])
        tickets.update_ticket(
            conn,
            ticket_id,
            {
                "owner": "售后-李工" if ticket["intent"] != "投诉" else "客户运营-赵倩",
                "status": "已解决",
                "root_cause": plan["root_cause"],
                "solution": plan["solution"],
                "satisfaction": plan["satisfaction"],
            },
        )
        # 首次响应时间：按工单顺序给 5/8/11/14… 分钟，便于看板展示平均首响
        created = datetime.fromisoformat(ticket["created_at"])
        conn.execute(
            "UPDATE tickets SET first_response_at = ? WHERE id = ?",
            ((created + timedelta(minutes=5 + 3 * index)).isoformat(timespec="seconds"), ticket_id),
        )
        conn.commit()
        resolved += 1

    # 知识回写闭环：挑已解决工单 -> FAQ 草稿 -> 审核发布；另留 1 条待审核
    published = None
    pending_draft = None
    for ticket_id in created_tickets:
        ticket = tickets.get_ticket(conn, ticket_id)
        if not ticket or ticket["status"] != "已解决":
            continue
        if ticket["intent"] == "其他" and published is None:
            draft = tickets.create_faq_draft(conn, ticket_id)
            published = tickets.publish_faq(conn, draft["id"])
        elif ticket["intent"] in DRAFT_ONLY and pending_draft is None:
            pending_draft = tickets.create_faq_draft(conn, ticket_id)

    # 造一单即将超时：把首响时限提前
    overdue = None
    for ticket_id in created_tickets[-2:]:
        ticket = tickets.get_ticket(conn, ticket_id)
        if ticket and ticket["status"] == "处理中":
            created = (datetime.now().astimezone() - timedelta(hours=6)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE tickets SET created_at = ?, sla_first_due = ?, first_response_at = NULL WHERE id = ?",
                (created, (datetime.now().astimezone() - timedelta(hours=5)).isoformat(timespec="seconds"), ticket_id),
            )
            conn.commit()
            overdue = ticket_id
            break

    db.log_event(conn, "demo.seeded", "", {"sessions": sessions, "tickets": len(created_tickets)})
    conn.commit()
    return {
        "ok": True,
        "faq": faq_count,
        "sessions": sessions,
        "messages": messages,
        "tickets": len(created_tickets),
        "resolved_tickets": resolved,
        "published_faq": published["id"] if published else None,
        "pending_draft": pending_draft["id"] if pending_draft else None,
        "overdue_ticket": overdue,
        "note": TRUTH,
    }


if __name__ == "__main__":
    connection = db.connect(config.DB_PATH)
    db.init_db(connection)
    print(seed_all(connection, reset=True))
