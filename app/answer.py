"""答复决策：检索 -> 置信度 -> 直接回答 / 追问 / 不猜 / 转人工。

原则（写进 README，面试可直接讲）：
1. 没有检索结果就不生成答案，不猜；
2. 置信度不足先追问车型与现象，而不是硬答；
3. 价格承诺、投诉、退订、返修纠纷、安全类问题必须人工接管；
4. 前两条知识冲突时优先「生效 + 更新时间更近」，仍分不出就转人工。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import config, kb, nlu

SOURCE_TEMPLATE = "\n\n依据：{source}（负责人 {owner}，更新于 {updated_at}）"


def _priority(analysis: dict[str, Any], status: str) -> str:
    if analysis["risk"]["level"] == "high":
        safety = any(w in analysis["matched_words"] for w in ("自燃", "起火", "失控", "刹车失灵", "冒烟", "事故"))
        return "P0" if analysis["intent"] == "投诉" or safety else "P1"
    if status == "no_hit":
        return "P1"
    return "P2"


def _handoff_reason(analysis: dict[str, Any], status: str, conflict: bool) -> str:
    reasons = list(analysis["risk"]["reasons"])
    if conflict:
        reasons.append("前两条 FAQ 答案冲突，需人工裁决")
    if status == "no_hit":
        reasons.append("知识库未命中，按规则不生成猜测答案")
    return "；".join(dict.fromkeys(reasons))


def _clarify_text(questions: list[str], anchor: str | None = None) -> str:
    if anchor:
        head = f"我理解您想咨询「{anchor}」，为确认答案准确，麻烦补充："
    else:
        head = "为了给你准确答案，先确认两点："
    return head + "\n" + "\n".join(f"· {q}" for q in questions)


def decide(conn: sqlite3.Connection, text: str) -> dict[str, Any]:
    analysis = nlu.analyze(text)
    hits = kb.search(conn, text)
    top = hits[0]["confidence"] if hits else 0.0
    conflict = kb.conflicts(hits)

    if config.LLM_ENABLED:  # pragma: no cover - M2，占位保持接口一致
        pass

    best = hits[0] if hits else None
    model_specific = bool(best) and config.ALL_MODELS not in (best["models"] or [])
    missing_required = [m for m in analysis["missing"] if m != "model" or model_specific]

    if analysis["risk"]["level"] == "high":
        status = "handoff"
    elif not hits or top < config.CLARIFY_THRESHOLD:
        status = "no_hit"
    elif conflict:
        status = "handoff"
    elif top < config.CONFIDENCE_THRESHOLD:
        status = "clarify"
    elif missing_required and top < 0.80:
        status = "clarify"
    else:
        status = "answered"

    best_scene = (best["scene"][:2] if best else "")
    evidence = [
        {
            "faq_id": hit["id"],
            "question": hit["question"],
            "answer": hit["answer"],
            "source": hit["source"],
            "owner": hit["owner"],
            "updated_at": hit["updated_at"],
            "confidence": hit["confidence"],
            "status": hit["status"],
        }
        for hit in hits[:3]
        if hit["confidence"] >= config.CLARIFY_THRESHOLD
        and (hit["scene"][:2] == best_scene or hit["confidence"] >= 0.65)
    ]

    if status == "answered":
        best = hits[0]
        answer = best["answer"] + SOURCE_TEMPLATE.format(
            source=best["source"], owner=best["owner"], updated_at=best["updated_at"]
        )
    elif status == "clarify":
        questions = nlu.clarify_questions(missing_required) or [
            "能再说得具体一点吗？比如车型、年份和具体现象。"
        ]
        answer = _clarify_text(questions, anchor=best["question"] if best else None)
    elif status == "handoff":
        answer = "这个问题需要人工同事跟进，我先帮你建工单，稍后由专人联系你。"
    else:
        answer = "这个问题知识库里暂时没有可靠答案。为避免答错，我先帮你转人工处理。"

    decision = {
        "text": text,
        "intent": analysis["intent"],
        "scene": analysis["scene"],
        "models": analysis["models"],
        "emotion": analysis["emotion"],
        "matched_words": analysis["matched_words"],
        "missing": analysis["missing"],
        "risk": analysis["risk"],
        "confidence": round(top, 3),
        "status": status,
        "conflict": conflict,
        "evidence": evidence,
        "answer": answer,
        "clarify": nlu.clarify_questions(missing_required) if status == "clarify" else [],
        "handoff_reason": _handoff_reason(analysis, status, conflict),
        "priority": _priority(analysis, status),
    }
    if status == "answered":
        kb.bump_hits(conn, [evidence[0]["faq_id"]])
        conn.commit()
    return decision
