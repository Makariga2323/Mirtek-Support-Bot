import pytest
from app.llm_engine import GenerationResult, validate_grounding

def test_validate_grounding_success():
    result = GenerationResult(
        can_answer=True,
        answer="Атлас стоит 450 рублей.",
        used_chunk_ids=["chunk-1"],
        confidence="high"
    )
    context = "Товар: Атлас. Цена: 450 руб."
    is_valid, reason = validate_grounding(result, context, ["chunk-1"])
    assert is_valid is True
    assert reason == "valid"

def test_validate_grounding_hallucinated_number():
    result = GenerationResult(
        can_answer=True,
        answer="Атлас стоит 999 рублей.",
        used_chunk_ids=["chunk-1"],
        confidence="high"
    )
    context = "Товар: Атлас. Цена: 450 руб."
    is_valid, reason = validate_grounding(result, context, ["chunk-1"])
    assert is_valid is False
    assert "V3_ungrounded_number_999" in reason

def test_validate_grounding_invalid_chunk():
    result = GenerationResult(
        can_answer=True,
        answer="Атлас стоит 450 рублей.",
        used_chunk_ids=["chunk-999"],
        confidence="high"
    )
    context = "Товар: Атлас. Цена: 450 руб."
    is_valid, reason = validate_grounding(result, context, ["chunk-1"])
    assert is_valid is False
    assert "V2_invalid_chunk_id" in reason