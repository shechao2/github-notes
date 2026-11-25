"""FAQ 知识库：加载、CRUD、FTS5 召回 + 覆盖度打分。

打分口径（可解释、可现场演示）：
    confidence = 0.55 * 标准问题覆盖 + 0.25 * 同义问法覆盖
               + 0.08 * 标签覆盖 + 0.05 * 答案覆盖 + 车型加权
其中「覆盖」= 问题里有多少二字片段被该字段命中（0~1）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterable

from . import config
from .db import dumps, now_iso, row_to_dict

JSON_FIELDS = ("variants", "models", "tags")

# 口语同义词：命中左词时，检索文本追加右词，弥补「维保/保养」这类说法差异
SYNONYMS: dict[str, list[str]] = {
    "维保": ["保养"],
    "保养": ["维保"],
    "补电": ["充电"],
    "充电": ["补电"],
    "掉电": ["续航"],
    "能跑多远": ["续航"],
    "打不着": ["亏电", "启动"],
    "不制冷": ["空调", "制冷"],
    "跳枪": ["充电", "中断"],
    "三包": ["质保"],
    "保修": ["质保"],
    "首保": ["保养"],
    "多少公里保养": ["保养周期"],
    "提车": ["交付"],
    "订车": ["交付"],
    "贷款": ["金融", "分期"],
    "月供": ["金融"],
    "绿牌": ["牌照", "上牌"],
}

_CLEAN_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def normalize(text: str) -> str:
    return _CLEAN_RE.sub("", (text or "").lower())


def grams(text: str, n: int = 2) -> set[str]:
    s = normalize(text)
    if not s:
        return set()
    if len(s) < n:
        return {s}
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def expand_synonyms(text: str) -> str:
    extra: list[str] = []
    for word, alts in SYNONYMS.items():
        if word in text:
            extra.extend(alts)
    return text + (" " + " ".join(dict.fromkeys(extra)) if extra else "")


def containment(q_grams: set[str], text: str) -> float:
    """问题覆盖度 = 0.7 * 二字片覆盖 + 0.3 * 单字覆盖（容忍「可以上门试驾吗」这类语序变化）。"""
    if not q_grams:
        return 0.0
    target = grams(text)
    if not target:
        return 0.0
    bigram = len(q_grams & target) / len(q_grams)

    q_chars = set("".join(sorted(q_grams)))
    target_chars = set(normalize(text))
    unigram = len(q_chars & target_chars) / len(q_chars) if q_chars else 0.0
    return 0.7 * bigram + 0.3 * unigram


def mentions_model(text: str) -> list[str]:
    low = normalize(text)
    found = []
    for model, aliases in config.MODELS.items():
        if any(normalize(alias) in low for alias in aliases):
            found.append(model)
    return found


def strip_models(text: str) -> str:
    """车型单独计权，不参与二字片段匹配，避免「X3 首付」把分母撑大。"""
    cleaned = text or ""
    for aliases in config.MODELS.values():
        for alias in sorted(aliases, key=len, reverse=True):
            cleaned = re.sub(re.escape(alias), " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def load_entries(conn: sqlite3.Connection, statuses: Iterable[str] = ("生效",)) -> list[dict[str, Any]]:
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT * FROM faq WHERE status IN ({placeholders})", tuple(statuses)
    ).fetchall()
    return [row_to_dict(row, JSON_FIELDS) for row in rows]


def get_entry(conn: sqlite3.Connection, faq_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM faq WHERE id = ?", (faq_id,)).fetchone()
    return row_to_dict(row, JSON_FIELDS) if row else None


def _fts_candidates(conn: sqlite3.Connection, text: str) -> set[str]:
    """FTS5(trigram) 召回候选 FAQ，命中不足 3 条时回退全量打分。"""
    tri = [g for g in grams(text, 3)]
    if not tri:
        return set()
    query = " OR ".join('"%s"' % g for g in tri[:12])
    try:
        rows = conn.execute(
            "SELECT DISTINCT entry_id FROM faq_fts WHERE faq_fts MATCH ?", (query,)
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {row[0] for row in rows}


def search(conn: sqlite3.Connection, text: str, top_k: int | None = None) -> list[dict[str, Any]]:
    top_k = top_k or config.TOP_K
    if not (text or "").strip():
        return []
    expanded = expand_synonyms(text)
    body = strip_models(text)
    q_grams = grams(body) or grams(text)
    q_grams_expanded = grams(expand_synonyms(body)) or q_grams
    q_models = mentions_model(text)
    normalized_query = normalize(body)

    entries = load_entries(conn)
    candidates = _fts_candidates(conn, expanded)
    if len(candidates) >= 3:
        entries = [e for e in entries if e["id"] in candidates] or entries

    scored: list[dict[str, Any]] = []
    for entry in entries:
        def score_for(query_grams: set[str]) -> float:
            q_cover = containment(query_grams, entry["question"])
            v_cover = containment(query_grams, " ".join(entry["variants"] or []))
            t_cover = containment(query_grams, " ".join(entry["tags"] or []))
            a_cover = containment(query_grams, entry["answer"])
            return 0.55 * q_cover + 0.25 * v_cover + 0.08 * t_cover + 0.05 * a_cover

        # 同义词扩展只加分、不摊薄分母：取原句与扩展句中的较高分
        score = max(score_for(q_grams), score_for(q_grams_expanded))

        # 完全命中标准问题 / 同义问法：直接给高置信度
        if normalized_query and normalized_query == normalize(entry["question"]):
            score = max(score, 0.90)
        elif normalized_query and normalized_query in {normalize(v) for v in (entry["variants"] or [])}:
            score = max(score, 0.86)

        entry_models = entry["models"] or []
        if config.ALL_MODELS in entry_models:
            score += 0.07
        elif q_models and set(q_models) & set(entry_models):
            score += 0.14
        elif q_models and entry_models:
            score -= 0.12

        # 极短问题容易虚高（「多少钱」「首保」），压到追问区间；带车型的不压
        if len(normalized_query) <= 3 and not q_models:
            score = min(score, 0.60)

        confidence = max(0.0, min(0.97, score))
        scored.append({**entry, "confidence": round(confidence, 3)})

    scored.sort(key=lambda e: (e["confidence"], e["updated_at"]), reverse=True)
    return scored[:top_k]


def conflicts(hits: list[dict[str, Any]]) -> bool:
    """高分知识之间答案不一致才算冲突；低分噪声不算。"""
    if len(hits) < 2:
        return False
    first, second = hits[0], hits[1]
    if first["confidence"] < 0.70:
        return False
    if first["confidence"] - second["confidence"] > 0.06:
        return False
    return normalize(first["answer"])[:24] != normalize(second["answer"])[:24]


def bump_hits(conn: sqlite3.Connection, faq_ids: Iterable[str]) -> None:
    for faq_id in faq_ids:
        conn.execute("UPDATE faq SET hits = hits + 1 WHERE id = ?", (faq_id,))


def upsert_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": entry["id"],
        "category": entry.get("category", "未分类"),
        "question": entry["question"],
        "variants": dumps(entry.get("variants") or []),
        "models": dumps(entry.get("models") or [config.ALL_MODELS]),
        "scene": entry.get("scene", ""),
        "answer": entry["answer"],
        "source": entry.get("source", "人工录入"),
        "owner": entry.get("owner", "待指派"),
        "updated_at": entry.get("updated_at") or now_iso()[:10],
        "status": entry.get("status", "生效"),
        "tags": dumps(entry.get("tags") or []),
        "priority": entry.get("priority", "P2"),
    }
    existing = conn.execute("SELECT id FROM faq WHERE id = ?", (entry["id"],)).fetchone()
    if existing:
        conn.execute(
            """UPDATE faq SET category=:category, question=:question, variants=:variants,
               models=:models, scene=:scene, answer=:answer, source=:source, owner=:owner,
               updated_at=:updated_at, status=:status, tags=:tags, priority=:priority
               WHERE id=:id""",
            payload,
        )
    else:
        payload["created_at"] = entry.get("created_at") or now_iso()
        conn.execute(
            """INSERT INTO faq(id, category, question, variants, models, scene, answer, source,
               owner, updated_at, status, tags, priority, created_at)
               VALUES(:id, :category, :question, :variants, :models, :scene, :answer, :source,
               :owner, :updated_at, :status, :tags, :priority, :created_at)""",
            payload,
        )
    rebuild_index(conn)
    conn.commit()
    return get_entry(conn, entry["id"]) or entry


def rebuild_index(conn: sqlite3.Connection) -> None:
    """重建 FTS 索引：把标准问题、同义问法、标签、分类拼成检索文本。"""
    conn.execute("DELETE FROM faq_fts")
    rows = conn.execute("SELECT * FROM faq WHERE status = '生效'").fetchall()
    for row in rows:
        entry = row_to_dict(row, JSON_FIELDS)
        text = " ".join(
            [
                entry["question"],
                " ".join(entry["variants"] or []),
                " ".join(entry["tags"] or []),
                entry["category"],
                entry["scene"],
            ]
        )
        conn.execute("INSERT INTO faq_fts(entry_id, text) VALUES(?, ?)", (entry["id"], text))
    conn.commit()


def next_faq_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT id FROM faq").fetchall()
    nums = [int(m.group(1)) for row in rows if (m := re.match(r"FAQ-(\d+)", row[0]))]
    return "FAQ-%03d" % (max(nums, default=0) + 1)


IMPORTED_AT = "2026-10-01T09:00:00+08:00"


def seed_from_file(conn: sqlite3.Connection, path: str, imported_at: str = IMPORTED_AT) -> int:
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    for entry in entries:
        entry.setdefault("created_at", imported_at)
        upsert_entry(conn, entry)
    if entries:
        conn.execute("DELETE FROM faq_fts")
        rebuild_index(conn)
    return len(entries)
