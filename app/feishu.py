"""飞书多维表格同步：建表、字段映射、按会话去重写入。

凭证来自 .env（FEISHU_APP_ID / FEISHU_APP_SECRET），首次使用：
    python -m app.feishu init     # 创建多维表格 + 16 个字段，打印表地址
    python -m app.feishu sync     # 把本地会话同步进去
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from . import config

BASE = "https://open.feishu.cn/open-apis"

# 字段名 -> 多维表格字段类型（1 文本 2 数字 3 单选 4 多选 5 日期 7 复选框 11 人员）
FIELD_SPEC: list[dict[str, Any]] = [
    {"field_name": "会话ID", "type": 1},
    {"field_name": "客户昵称", "type": 1},
    {"field_name": "性别", "type": 3, "property": {"options": [{"name": "男"}, {"name": "女"}, {"name": "未提供"}]}},
    {"field_name": "平台", "type": 3, "property": {"options": [{"name": "官网"}, {"name": "App"}, {"name": "门店"}, {"name": "400"}]}},
    {"field_name": "客层", "type": 3, "property": {"options": [{"name": "新客"}, {"name": "老客"}, {"name": "待识别"}]}},
    {"field_name": "咨询意图", "type": 1},
    {"field_name": "情绪状态", "type": 3, "property": {"options": [{"name": "平稳"}, {"name": "犹豫"}, {"name": "负面"}, {"name": "待确认"}]}},
    {"field_name": "转化状态", "type": 3, "property": {"options": [{"name": "待跟进"}, {"name": "明确意向"}, {"name": "已下单"}, {"name": "售后处理"}]}},
    {"field_name": "标签", "type": 4, "property": {"options": []}},
    {"field_name": "询问商品", "type": 1},
    {"field_name": "售价", "type": 2},
    {"field_name": "时间", "type": 5},
    {"field_name": "问题状态", "type": 3, "property": {"options": [{"name": "待处理"}, {"name": "跟进中"}, {"name": "已解决"}, {"name": "已关闭"}]}},
    {"field_name": "负责人", "type": 1},
    {"field_name": "优先级", "type": 3, "property": {"options": [{"name": "P0"}, {"name": "P1"}, {"name": "P2"}]}},
    {"field_name": "SLA节点", "type": 5},
]

# 车型起步价（万元），用于「售价」字段；未提及车型则留空
MODEL_PRICE: dict[str, float] = {"X3": 13.98, "S7": 19.98, "L9": 28.98}

_token_cache: dict[str, Any] = {"token": "", "expire_at": 0.0}


class FeishuError(RuntimeError):
    def __init__(self, code: int, msg: str):
        super().__init__(f"飞书接口错误 {code}: {msg}")
        self.code = code


def _request(method: str, path: str, payload: dict | None = None, token: str | None = None) -> dict:
    token = token or tenant_token()
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # 4xx 也带业务 code
        body = json.loads(exc.read().decode() or "{}")
    if body.get("code") not in (0, None):
        raise FeishuError(body.get("code"), body.get("msg", "unknown"))
    return body.get("data", {})


def tenant_token() -> str:
    now = time.time()
    if _token_cache["token"] and _token_cache["expire_at"] > now + 60:
        return _token_cache["token"]
    if not (config.FEISHU_APP_ID and config.FEISHU_APP_SECRET):
        raise FeishuError(-1, "未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

    payload = json.dumps(
        {"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET}
    ).encode()
    req = urllib.request.Request(
        BASE + "/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode())
    if body.get("code") != 0:
        raise FeishuError(body.get("code", -1), body.get("msg", "取 token 失败"))
    _token_cache.update({"token": body["tenant_access_token"], "expire_at": now + int(body.get("expire", 7200))})
    return _token_cache["token"]


def create_base(name: str = "星驰客户资产池") -> dict[str, Any]:
    data = _request("POST", "/bitable/v1/apps", {"name": name})
    app = data.get("app", {})
    return {"app_token": app.get("app_token"), "url": app.get("url"), "name": app.get("name")}


def create_table(app_token: str, table_name: str = "客户会话记录") -> str:
    data = _request(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        {"table": {"name": table_name, "default_view_name": "全部会话", "fields": FIELD_SPEC}},
    )
    return data["table_id"]


def list_records(app_token: str, table_id: str, page_size: int = 500) -> list[dict]:
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size={min(page_size, 500)}"
    return _request("GET", path).get("items", [])


def upsert_record(app_token: str, table_id: str, key: str, fields: dict[str, Any]) -> str:
    """按「会话ID」去重：存在则更新，不存在则新增。返回 record_id。"""
    for item in list_records(app_token, table_id):
        item_fields = item.get("fields", {})
        value = item_fields.get("会话ID")
        if isinstance(value, list):  # 文本字段可能以富文本数组返回
            value = "".join(part.get("text", "") for part in value if isinstance(part, dict))
        if value == key:
            record_id = item["record_id"]
            _request("PUT", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}", {"fields": fields})
            return record_id
    data = _request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", {"fields": fields})
    return data["record"]["record_id"]


def _ms(value: str | None) -> int | None:
    if not value:
        return None
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


def session_fields(session: dict[str, Any], ticket: dict[str, Any] | None = None) -> dict[str, Any]:
    """把本地会话映射成 16 个飞书字段（纯函数，方便测试）。"""
    messages = session.get("messages", [])
    ai_messages = [m for m in messages if m.get("role") == "ai"]
    last_ai = ai_messages[-1] if ai_messages else {}
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    decision = last_ai.get("decision") or {}
    ticket = ticket or (session.get("tickets") or [None])[0]

    # 车型取整段会话的并集，避免「最后一句没提车型」导致画像丢失
    models: list[str] = []
    for message in ai_messages:
        for model in (message.get("decision") or {}).get("models", []) or []:
            if model not in models:
                models.append(model)

    intent = decision.get("intent") or last_ai.get("intent") or "其他"
    if ticket:
        status = {"已解决": "已解决", "处理中": "跟进中", "已关闭": "已关闭"}.get(ticket.get("status"), "待处理")
    elif last_ai.get("status") in {"accepted", "edited"}:
        status = "已解决"
    else:
        status = "待处理"

    conversion = "售后处理" if decision.get("scene") == "售后" else (
        "明确意向" if intent in {"价格咨询", "金融方案", "试驾", "交付", "置换"} else "待跟进"
    )
    sla_due = (ticket or {}).get("sla_resolve_due") or (ticket or {}).get("sla_first_due")
    owner = (ticket or {}).get("owner") or ("AI 自动" if status == "已解决" else "未分配")
    price = MODEL_PRICE.get(models[0]) if models else None

    return {
        "会话ID": session.get("id", ""),
        "客户昵称": session.get("customer", ""),
        "性别": "未提供",
        "平台": session.get("channel") or "官网",
        "客层": "新客",
        "咨询意图": intent,
        "情绪状态": decision.get("emotion") or "待确认",
        "转化状态": conversion,
        "标签": models or ["未提及车型"],
        "询问商品": "、".join(models),
        "售价": price,
        "时间": _ms(last_user.get("created_at")),
        "问题状态": status,
        "负责人": owner,
        "优先级": (ticket or {}).get("priority") or decision.get("priority") or "P2",
        "SLA节点": _ms(sla_due),
    }


def sync_session(session: dict[str, Any], app_token: str | None = None, table_id: str | None = None) -> dict[str, Any]:
    app_token = app_token or config.FEISHU_BITABLE_TOKEN
    table_id = table_id or config.FEISHU_TABLE_ID
    if not (app_token and table_id):
        result = init_base()
        app_token, table_id = result["app_token"], result["table_id"]
    fields = session_fields(session)
    record_id = upsert_record(app_token, table_id, fields["会话ID"], fields)
    return {"app_token": app_token, "table_id": table_id, "record_id": record_id, "fields": fields}


def init_base(name: str = "星驰客户资产池") -> dict[str, Any]:
    base = create_base(name)
    table_id = create_table(base["app_token"])
    _persist(base["app_token"], table_id)
    return {**base, "table_id": table_id}


def _persist(app_token: str, table_id: str) -> None:
    """把 app_token / table_id 写回 .env，避免每次重新建表。"""
    env_path = os.path.join(config.BASE_DIR, ".env")
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            lines = [line for line in fh.read().splitlines()
                     if not line.startswith(("FEISHU_BITABLE_TOKEN", "FEISHU_TABLE_ID"))]
    lines += [f"FEISHU_BITABLE_TOKEN={app_token}", f"FEISHU_TABLE_ID={table_id}"]
    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    config.FEISHU_BITABLE_TOKEN = app_token
    config.FEISHU_TABLE_ID = table_id


def _main(argv: list[str]) -> int:
    from . import db

    action = argv[1] if len(argv) > 1 else "init"
    if action == "init":
        print(json.dumps(init_base(), ensure_ascii=False, indent=2))
        return 0
    if action == "sync":
        conn = db.connect(config.DB_PATH)
        rows = conn.execute("SELECT id FROM sessions ORDER BY updated_at DESC").fetchall()
        from .service import get_session

        ok = 0
        for row in rows:
            session = get_session(conn, row["id"])
            sync_session(session)
            ok += 1
        print(json.dumps({"synced": ok}, ensure_ascii=False))
        return 0
    print("用法：python -m app.feishu init | sync")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
