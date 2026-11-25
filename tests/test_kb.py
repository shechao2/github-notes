"""知识库检索与治理：召回、置信度、冲突、状态流转。"""
from app import kb


def test_search_hits_expected_faq(conn):
    hits = kb.search(conn, "S7 续航多少")
    assert hits[0]["id"] == "FAQ-002"
    assert hits[0]["confidence"] > 0.8


def test_standard_question_exact_match_scores_high(conn):
    hits = kb.search(conn, "新车多久做首保")
    assert hits[0]["id"] == "FAQ-029"
    assert hits[0]["confidence"] >= 0.85


def test_synonym_phrasing_still_found(conn):
    """「维保周期」不是任何一条标准问题，靠同义问法命中。"""
    hits = kb.search(conn, "维保周期")
    assert hits[0]["id"] == "FAQ-030"


def test_short_query_is_capped(conn):
    hits = kb.search(conn, "多少钱")
    assert hits[0]["confidence"] <= 0.6


def test_unrelated_query_scores_low(conn):
    hits = kb.search(conn, "你们公司股票代码是多少")
    assert hits[0]["confidence"] < 0.3


def test_model_mismatch_is_penalised(conn):
    """问 X3 的续航，不能拿 S7 的续航当答案。"""
    hits = kb.search(conn, "X3 续航多少")
    assert hits[0]["id"] == "FAQ-003"


def test_conflict_detection_requires_confidence(conn):
    assert kb.conflicts([{"confidence": 0.92, "answer": "答案甲" * 10},
                         {"confidence": 0.90, "answer": "答案乙" * 10}]) is True
    assert kb.conflicts([{"confidence": 0.5, "answer": "答案甲" * 10},
                         {"confidence": 0.48, "answer": "答案乙" * 10}]) is False
    assert kb.conflicts([{"confidence": 0.92, "answer": "同一个答案" * 10},
                         {"confidence": 0.90, "answer": "同一个答案" * 10}]) is False


def test_draft_faq_not_searchable_until_published(conn):
    draft = kb.upsert_entry(conn, {
        "id": "FAQ-900",
        "question": "试驾车能上高速吗",
        "answer": "试驾车须由顾问陪同，按规定路线行驶。",
        "category": "售前",
        "status": "待审核",
        "tags": ["试驾"],
    })
    assert draft["status"] == "待审核"
    assert "FAQ-900" not in kb._fts_candidates(conn, "试驾车能上高速吗")

    conn.execute("UPDATE faq SET status = '生效' WHERE id = 'FAQ-900'")
    kb.rebuild_index(conn)
    conn.commit()
    hits = kb.search(conn, "试驾车能上高速吗")
    assert hits[0]["id"] == "FAQ-900"


def test_next_faq_id_is_sequential(conn):
    assert kb.next_faq_id(conn) == "FAQ-070"
