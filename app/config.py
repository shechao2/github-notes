"""全局配置：阈值、SLA 规则、车型词表。

所有可调项都支持环境变量覆盖，方便演示时现场改阈值。
"""
from __future__ import annotations

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path: str) -> None:
    """极简 .env 读取：不引入额外依赖，已存在的环境变量优先。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))

BRAND = "星驰汽车"

# 车型词表：canonical -> 别名（用于从自然语言里抽车型）
MODELS: dict[str, list[str]] = {
    "S7": ["s7", "星驰s7", "s7后驱", "s7四驱"],
    "X3": ["x3", "星驰x3"],
    "L9": ["l9", "星驰l9"],
}

ALL_MODELS = "全车型"

# 置信度阈值：>= answer 直接答；[clarify, answer) 追问；< clarify 不猜、转人工
CONFIDENCE_THRESHOLD = float(os.environ.get("A4S_CONF_THRESHOLD", "0.62"))
CLARIFY_THRESHOLD = float(os.environ.get("A4S_CLARIFY_THRESHOLD", "0.30"))
TOP_K = int(os.environ.get("A4S_TOP_K", "5"))

# 工单 SLA：优先级 -> (首次响应分钟, 解决时限分钟)
SLA_RULES: dict[str, tuple[int, int]] = {
    "P0": (15, 240),
    "P1": (30, 480),
    "P2": (120, 2880),
}

# 必须转人工的意图：价格承诺、投诉、退订、返修纠纷
MANDATORY_HANDOFF_INTENTS = {"投诉", "退订退款", "价格承诺", "维修纠纷"}

# 高风险关键词：命中即人工接管，不自动作答
RISK_KEYWORDS = [
    "投诉", "起诉", "律师", "曝光", "12315", "差评", "退车", "退订", "退款",
    "自燃", "起火", "冒烟", "刹车失灵", "失控", "事故", "人身", "赔偿",
    "底价", "最低价", "保证", "承诺",
]

# 情绪词
NEGATIVE_WORDS = ["投诉", "太差", "垃圾", "骗", "欺骗", "生气", "失望", "差评", "曝光", "起诉", "退钱", "恶心"]
HESITANT_WORDS = ["考虑", "再看看", "对比", "犹豫", "贵了", "有点贵", "再想想", "纠结"]

# 需要追问的信息要素
CLARIFY_QUESTIONS = {
    "model": "请问是哪款车型（X3 / S7 / L9）？",
    "symptom": "能具体描述一下现象吗？比如什么时候出现、仪表有没有报警？",
    "scene": "这是售前咨询还是用车售后问题？",
}

DB_PATH = os.environ.get("A4S_DB") or os.path.join(BASE_DIR, "data", "app.db")
FAQ_FILE = os.path.join(BASE_DIR, "data", "faq.json")

# LLM 开关：M1 用规则生成，配置 key 后可切换为「只依据检索结果作答」
LLM_ENABLED = os.environ.get("A4S_LLM", "0") == "1"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.environ.get("A4S_LLM_MODEL", "deepseek-chat")

# 飞书自建应用与多维表格（M2）：凭证只放 .env
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_BITABLE_TOKEN = os.environ.get("FEISHU_BITABLE_TOKEN", "")
FEISHU_TABLE_ID = os.environ.get("FEISHU_TABLE_ID", "")
