"""轻量 NLU：意图、场景、车型、情绪、风险、信息完整度。

故意做成规则可读的版本：面试时可以指着关键词表讲「为什么这个词会转人工」，
而不是一句「模型自己判断的」。
"""
from __future__ import annotations

from typing import Any

from . import config

# 顺序敏感：越靠前优先级越高（投诉/退订/价格承诺必须先命中）
INTENT_RULES: list[tuple[str, str, list[str]]] = [
    ("投诉", "边界", ["投诉", "服务态度", "曝光", "12315", "差评", "起诉", "没人管", "推诿"]),
    ("退订退款", "边界", ["退订", "退车", "退款", "不想要了", "定金能退", "订金退"]),
    ("价格承诺", "边界", ["底价", "最低价", "便宜点", "能不能少", "给我个价", "优惠多少"]),
    ("维修纠纷", "边界", ["返修", "修了还坏", "没修好", "维修纠纷", "又坏了"]),
    ("救援", "售后", ["救援", "拖车", "搭电", "趴窝", "打不着"]),
    ("事故保险", "售后", ["事故", "保险", "理赔", "定损", "碰撞", "撞了"]),
    ("质保政策", "售后", ["质保", "保修", "三包", "衰减", "保修期"]),
    ("补能充电", "售后", ["充电", "快充", "慢充", "充电桩", "续航", "补能", "跳枪", "预热"]),
    ("保养", "售后", ["保养", "首保", "维保", "多少公里保养", "小保养"]),
    ("故障维修", "售后", ["故障", "异响", "不制冷", "黑屏", "报警", "故障码", "失灵", "卡顿", "亏电", "坏", "响", "抖"]),
    ("金融方案", "售前", ["首付", "分期", "贷款", "月供", "利率", "征信", "贷"]),
    ("置换", "售前", ["置换", "旧车", "换购", "抵多少"]),
    ("试驾", "售前", ["试驾", "看车", "上门试驾"]),
    ("交付", "售前", ["交付", "提车", "现车", "等多久", "订车", "锁单"]),
    ("车型配置", "售前", ["增程", "纯电", "动力", "四驱", "电池多大", "马力", "变速箱", "几个座", "几座"]),
    ("价格咨询", "售前", ["多少钱", "价格", "售价", "指导价", "落地", "贵不贵"]),
    ("智驾配置", "售前", ["智驾", "辅助驾驶", "自动泊车", "配置", "自动驾驶"]),
    ("车机OTA", "售后", ["ota", "车机", "升级", "中控", "屏幕"]),
    ("选装颜色", "售前", ["颜色", "内饰", "选装", "配色"]),
    ("政策上牌", "售前", ["购置税", "上牌", "绿牌", "牌照", "年检", "免税"]),
    ("竞品对比", "售前", ["竞品", "比亚迪", "特斯拉", "对比", "哪家好"]),
    ("隐私合规", "边界", ["隐私", "数据安全", "聊天记录", "个人信息"]),
]

# 故障现象词：用于判断「售后故障是否描述得足够具体」
SYMPTOM_WORDS = ["响", "抖", "黑屏", "不制冷", "跳枪", "报警", "故障码", "亏电", "打不着", "异味", "冒烟", "卡", "慢"]

HIGH_RISK_WORDS = config.RISK_KEYWORDS


def _hit(text: str, words: list[str]) -> list[str]:
    low = text.lower()
    return [w for w in words if w.lower() in low]


def analyze(text: str) -> dict[str, Any]:
    text = text or ""
    intent, scene, matched = "其他", "其他", []
    for name, area, words in INTENT_RULES:
        hits = _hit(text, words)
        if hits:
            intent, scene, matched = name, area, hits
            break

    models = [m for m, aliases in config.MODELS.items() if _hit(text, aliases)]

    negative = _hit(text, config.NEGATIVE_WORDS)
    hesitant = _hit(text, config.HESITANT_WORDS)
    emotion = "负面" if negative else ("犹豫" if hesitant else "平稳")

    risk_reasons: list[str] = []
    if _hit(text, HIGH_RISK_WORDS):
        risk_reasons.append("命中高风险词：" + "、".join(_hit(text, HIGH_RISK_WORDS)))
    if intent in config.MANDATORY_HANDOFF_INTENTS:
        risk_reasons.append(f"意图「{intent}」必须人工接管")
    if emotion == "负面":
        risk_reasons.append("客户情绪为负面")

    missing: list[str] = []
    if not models and intent in {"故障维修", "补能充电", "质保政策", "保养", "价格咨询", "金融方案", "置换", "交付"}:
        missing.append("model")
    if intent == "故障维修" and not _hit(text, SYMPTOM_WORDS):
        missing.append("symptom")

    return {
        "intent": intent,
        "scene": scene,
        "models": models,
        "emotion": emotion,
        "matched_words": matched,
        "negative_words": negative,
        "risk": {"level": "high" if risk_reasons else "low", "reasons": risk_reasons},
        "missing": missing,
        "symptom": _hit(text, SYMPTOM_WORDS),
    }


def clarify_questions(missing: list[str]) -> list[str]:
    return [config.CLARIFY_QUESTIONS[key] for key in missing if key in config.CLARIFY_QUESTIONS]
