import json
import re
import time
from typing import Dict, Any, List, Tuple, Optional
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

class IntentResult(BaseModel):
    intent_id: str = Field(description="ID намерения, например check_stock")
    category: str = Field(description="Категория, например product_availability")
    entities: Dict[str, Any] = Field(default_factory=dict, description="Сущности (артикул, магазин, дата)")
    rewritten_query: str = Field(description="Переформулированный запрос для поиска")
    confidence: float = Field(description="Уверенность от 0 до 1")

class GenerationResult(BaseModel):
    can_answer: bool = Field(description="Можем ли ответить на вопрос на основе данных")
    answer: str = Field(description="Текст ответа клиенту")
    used_chunk_ids: List[str] = Field(description="ID использованных фрагментов данных")
    confidence: str = Field(description="low, medium или high")

class LLMEngine:
    def __init__(self, base_url: str, api_key: str, model_name: str):
        self.llm = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            temperature=0,
            max_retries=1
        )
        self.intent_parser = PydanticOutputParser(pydantic_object=IntentResult)
        self.gen_parser = PydanticOutputParser(pydantic_object=GenerationResult)

    def _invoke_with_retry(self, chain, inputs: dict) -> Any:
        try:
            return chain.invoke(inputs)
        except Exception:
            time.sleep(0.5)
            return chain.invoke(inputs)

    def classify_intent(self, text: str, examples: List[dict]) -> IntentResult:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Ты классификатор обращений магазина тканей. Верни JSON строго по схеме.\n{format_instructions}\nПримеры намерений: {examples}"),
            ("user", "{text}")
        ])
        
        chain = prompt | self.llm | self.intent_parser
        inputs = {
            "text": text,
            "format_instructions": self.intent_parser.get_format_instructions(),
            "examples": json.dumps(examples, ensure_ascii=False)
        }
        return self._invoke_with_retry(chain, inputs)

    def generate_answer(self, question: str, context: str) -> GenerationResult:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Ты ассистент магазина тканей. Ответь на вопрос, опираясь ТОЛЬКО на предоставленный контекст. Если данных нет, верни can_answer=false.\n{format_instructions}\nКонтекст: {context}"),
            ("user", "{question}")
        ])
        
        chain = prompt | self.llm | self.gen_parser
        inputs = {
            "question": question,
            "context": context,
            "format_instructions": self.gen_parser.get_format_instructions()
        }
        return self._invoke_with_retry(chain, inputs)

def extract_numbers(text: str) -> List[str]:
    text = text.replace(" ", "")
    pattern = r'\d+(?:[.,]\d+)?'
    return re.findall(pattern, text)

def validate_grounding(result: GenerationResult, context: str, valid_chunk_ids: List[str]) -> Tuple[bool, str]:
    if not result.can_answer:
        return False, "cannot_answer"
        
    if not result.answer or len(result.answer) > 1000:
        return False, "V1_invalid_answer_length"
        
    if not result.used_chunk_ids:
        return False, "V2_no_used_chunks"
        
    for chunk_id in result.used_chunk_ids:
        if chunk_id not in valid_chunk_ids:
            return False, "V2_invalid_chunk_id"

    answer_numbers = extract_numbers(result.answer)
    context_numbers = extract_numbers(context)
    for num in answer_numbers:
        if num not in context_numbers:
            return False, f"V3_ungrounded_number_{num}"

    return True, "valid"