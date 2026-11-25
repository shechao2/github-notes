"""M2：把检索到的 FAQ 证据交给大模型改写成客服口吻。

护栏（比「让模型直接答」重要）：
1. 提示词只允许依据给定资料，不许编造价格/优惠/承诺；
2. 生成结果里的数字必须在证据里出现过，否则判定「无依据」，降级为规则拼接；
3. 模型判断资料不足时输出 NEED_CLARIFY / NEED_HANDOFF，由上层转追问或工单；
4. 网络失败、超时、额度不足一律回退，不影响主流程。
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from . import config

SYSTEM_PROMPT = """你是星驰汽车 4S 店的在线客服，负责售前咨询和售后维保。
规则：
1. 只能依据【资料】回答，不许使用资料以外的信息；
2. 不许编造价格、优惠、保修承诺，不许承诺具体的赔付金额或时限；
3. 用口语化中文，2—4 句，先回答客户的问题；
4. 如果资料不足以回答，只输出 NEED_CLARIFY（需要客户补充车型/年份/现象）；
5. 如果问题涉及价格承诺、投诉、退订、安全或纠纷，只输出 NEED_HANDOFF；
6. 不要输出「根据资料」这类元话术，直接像客服一样说话。"""

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def available() -> bool:
    return bool(config.LLM_ENABLED and config.DEEPSEEK_API_KEY)


def numbers(text: str) -> set[str]:
    return set(_NUM_RE.findall(text or ""))


def grounded(answer_text: str, evidence: list[dict[str, Any]]) -> bool:
    """答案里的数字必须能在证据中找到，防止模型编保修期、价格、里程。"""
    allowed: set[str] = set()
    for item in evidence:
        allowed |= numbers(item.get("answer", ""))
        allowed |= numbers(item.get("question", ""))
    return numbers(answer_text) <= allowed


def build_messages(question: str, analysis: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    docs = "\n\n".join(
        f"[{item['faq_id']}] {item['question']}\n{item['answer']}\n（来源：{item['source']}；负责人：{item['owner']}）"
        for item in evidence
    )
    context = (
        f"客户问题：{question}\n"
        f"识别结果：意图={analysis.get('intent')}，场景={analysis.get('scene')}，"
        f"车型={'、'.join(analysis.get('models') or []) or '未提及'}，情绪={analysis.get('emotion')}\n\n"
        f"【资料】\n{docs}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]


def compose(question: str, analysis: dict[str, Any], evidence: list[dict[str, Any]], timeout: float = 20.0) -> dict[str, Any]:
    if not available():
        raise RuntimeError("LLM 未启用或缺少 DEEPSEEK_API_KEY")
    payload = {
        "model": config.LLM_MODEL,
        "messages": build_messages(question, analysis, evidence),
        "temperature": 0.2,
        "max_tokens": 400,
        "stream": False,
    }
    response = httpx.post(
        config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()

    if text.startswith("NEED_HANDOFF"):
        return {"status": "handoff", "text": text}
    if text.startswith("NEED_CLARIFY"):
        return {"status": "clarify", "text": text}
    if not text:
        return {"status": "empty", "text": ""}
    return {"status": "ok", "text": text, "model": body.get("model", config.LLM_MODEL), "usage": body.get("usage", {})}


def _main(argv: list[str]) -> int:
    """python -m app.llm "S7 续航多少"  —— 手动验证模型改写效果"""
    import json
    import sys as _sys

    from . import db, kb, nlu

    question = argv[1] if len(argv) > 1 else "S7 续航多少"
    conn = db.connect(config.DB_PATH)
    hits = kb.search(conn, question)
    evidence = [
        {"faq_id": h["id"], "question": h["question"], "answer": h["answer"],
         "source": h["source"], "owner": h["owner"], "updated_at": h["updated_at"]}
        for h in hits[:3]
    ]
    analysis = nlu.analyze(question)
    try:
        out = compose(question, analysis, evidence)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    grounded_ok = out.get("status") == "ok" and grounded(out["text"], evidence)
    print(json.dumps({
        "question": question,
        "model_status": out.get("status"),
        "rewritten": out.get("text"),
        "grounded": grounded_ok,
        "usage": out.get("usage"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv))
