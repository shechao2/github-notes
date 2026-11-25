"""轻量 NLU：意图、场景、车型、情绪、风险、信息完整度。"""
from app import nlu


def test_intent_and_scene_for_price_question():
    result = nlu.analyze("S7 多少钱")
    assert result["intent"] == "价格咨询"
    assert result["scene"] == "售前"
    assert result["models"] == ["S7"]


def test_complaint_is_high_risk_and_boundary():
    result = nlu.analyze("我要投诉门店服务态度")
    assert result["intent"] == "投诉"
    assert result["scene"] == "边界"
    assert result["risk"]["level"] == "high"


def test_negative_emotion_detected():
    result = nlu.analyze("太差了，服务一直推诿")
    assert result["emotion"] == "负面"
    assert result["risk"]["level"] == "high"


def test_maintenance_intent_without_model():
    result = nlu.analyze("保养一次多少钱")
    assert result["intent"] == "保养"
    assert "model" in result["missing"]


def test_fault_intent_requires_symptom():
    result = nlu.analyze("车坏了")
    assert result["intent"] == "故障维修"
    assert "symptom" in result["missing"]


def test_symptom_present_clears_clarify_need():
    result = nlu.analyze("S7 空调不制冷")
    assert result["models"] == ["S7"]
    assert "symptom" not in result["missing"]


def test_clarify_questions_map_to_missing_items():
    questions = nlu.clarify_questions(["model", "symptom"])
    assert len(questions) == 2
    assert all(q.endswith("？") for q in questions)
