import sys
from typing import Any, Optional

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from agent.state import AgentState, UserCondition
from tools.search_tool import search_properties
from tools.filter_tool import filter_and_score


class ConditionSchema(BaseModel):
    region: Optional[str] = Field(None, description="희망 지역명 (예: 마포구, 강남구, 서울 마포구)")
    deal_type: Optional[str] = Field(None, description="거래 유형: '월세', '전세', '매매' 중 하나만")
    max_deposit: Optional[int] = Field(None, description="최대 보증금/전세금 (만원 단위 숫자)")
    max_monthly: Optional[int] = Field(None, description="최대 월세 (만원 단위 숫자, 월세 거래일 때만)")
    max_price: Optional[int] = Field(None, description="최대 매매가 (만원 단위 숫자, 매매 거래일 때만)")
    min_area: Optional[float] = Field(None, description="최소 면적 (m² 단위 숫자)")
    property_type: Optional[str] = Field(None, description="방 종류: '원룸', '투룸', '쓰리룸', '아파트', '오피스텔' 중 하나")


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0)


def parse_condition_node(state: AgentState) -> AgentState:
    """사용자 자연어 입력을 파싱하여 UserCondition을 추출합니다."""
    llm = _get_llm().with_structured_output(ConditionSchema)

    system_prompt = (
        "사용자의 부동산 검색 조건을 분석하여 구조화된 형태로 추출하세요.\n"
        "- 지역명은 '마포구', '강남구', '서울 마포구' 형태로 추출\n"
        "- 거래 유형은 반드시 '월세', '전세', '매매' 중 하나로만 지정\n"
        "- '월세 보증금'이 언급되더라도 deal_type은 명시적으로 언급된 것 우선 (매매/전세/월세)\n"
        "- 금액은 만원 단위 숫자로만 반환 (예: '3000만원' → 3000)\n"
        "- 명시되지 않은 필드는 null"
    )

    messages = state.get("messages", [])
    invoke_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=state["user_input"]),
    ]

    try:
        parsed: ConditionSchema = llm.invoke(invoke_messages)
        condition: UserCondition = {
            "region": parsed.region,
            "deal_type": parsed.deal_type,
            "max_deposit": parsed.max_deposit,
            "max_monthly": parsed.max_monthly,
            "max_price": parsed.max_price,
            "min_area": parsed.min_area,
            "property_type": parsed.property_type,
        }
    except Exception:
        condition = {
            "region": None,
            "deal_type": None,
            "max_deposit": None,
            "max_monthly": None,
            "max_price": None,
            "min_area": None,
            "property_type": None,
        }

    updated_messages = list(messages) + [
        HumanMessage(content=state["user_input"]),
        AIMessage(content=str(condition)),
    ]

    return {
        **state,
        "condition": condition,
        "messages": updated_messages,
    }


def validate_node(state: AgentState) -> AgentState:
    """조건의 유효성을 검증합니다."""
    condition = state.get("condition", {})
    retry_count = state.get("retry_count", 0)

    # retry_count >= 2이면 강제 통과
    if retry_count >= 2:
        return {**state, "is_valid": True, "error_message": None}

    has_region = bool(condition.get("region"))
    has_deal_type = bool(condition.get("deal_type"))

    if has_region or has_deal_type:
        return {**state, "is_valid": True, "error_message": None}
    else:
        return {
            **state,
            "is_valid": False,
            "error_message": "지역 또는 거래 유형이 필요합니다.",
        }


def clarify_node(state: AgentState) -> AgentState:
    """부족한 조건을 사용자에게 질문합니다."""
    llm = _get_llm()
    condition = state.get("condition", {})

    missing = []
    if not condition.get("region"):
        missing.append("희망 지역")
    if not condition.get("deal_type"):
        missing.append("거래 유형(월세/전세/매매)")

    system_prompt = (
        "부동산 상담 AI입니다. 사용자가 부동산을 검색하려 하는데 "
        f"다음 정보가 부족합니다: {', '.join(missing)}. "
        "친절하게 해당 정보를 물어봐 주세요. 짧고 명확하게."
    )

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_input"]),
        ])
        question = response.content
    except Exception:
        question = f"죄송합니다. {', '.join(missing)}을(를) 알려주시면 더 정확한 매물을 찾아드릴 수 있습니다."

    print(f"\n🤔 추가 정보 필요: {question}")
    print("👉 ", end="", flush=True)

    try:
        additional_input = sys.stdin.readline().strip()
        if not additional_input:
            additional_input = "서울 마포구 월세"
    except EOFError:
        additional_input = "서울 마포구 월세"

    combined_input = f"{state['user_input']} {additional_input}"

    return {
        **state,
        "user_input": combined_input,
        "retry_count": state.get("retry_count", 0) + 1,
    }


def search_and_filter_node(state: AgentState) -> AgentState:
    """매물을 검색하고 필터링합니다."""
    condition = state.get("condition", {})

    region = condition.get("region")
    deal_type = condition.get("deal_type")
    property_type = condition.get("property_type")

    search_results = search_properties.invoke({
        "region": region,
        "deal_type": deal_type,
        "property_type": property_type,
    })

    # 결과가 없으면 region만으로 재검색
    if not search_results and region:
        search_results = search_properties.invoke({"region": region, "deal_type": None, "property_type": None})

    # 그래도 없으면 deal_type만으로 재검색
    if not search_results and deal_type:
        search_results = search_properties.invoke({"region": None, "deal_type": deal_type, "property_type": None})

    filtered_results = filter_and_score.invoke({"properties": search_results, "condition": condition})

    return {
        **state,
        "search_results": search_results,
        "filtered_results": filtered_results,
    }


def recommend_node(state: AgentState) -> AgentState:
    """필터링된 매물을 바탕으로 자연어 추천 텍스트를 생성합니다."""
    llm = _get_llm()
    filtered = state.get("filtered_results", [])
    condition = state.get("condition", {})

    if not filtered:
        recommendations = (
            "😔 죄송합니다. 입력하신 조건에 맞는 매물을 찾을 수 없습니다.\n"
            "조건을 조금 완화하시거나 다른 지역/거래유형으로 다시 검색해 보세요."
        )
        return {**state, "recommendations": recommendations}

    props_text = json.dumps(filtered, ensure_ascii=False, indent=2)
    condition_text = json.dumps(condition, ensure_ascii=False)

    system_prompt = (
        "당신은 친절한 부동산 추천 전문가입니다. "
        "아래 매물 목록을 바탕으로 사용자에게 추천 멘트를 작성해주세요. "
        "각 매물의 장단점과 추천 이유를 포함하고, "
        "가장 추천하는 매물 1순위를 명시해주세요. "
        "이모지를 활용하여 가독성 있게 작성하세요."
    )

    user_message = (
        f"사용자 조건: {condition_text}\n\n"
        f"추천 매물 목록:\n{props_text}\n\n"
        "위 매물들에 대해 추천 분석을 작성해주세요."
    )

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        recommendations = response.content
    except Exception as e:
        recommendations = f"추천 생성 중 오류가 발생했습니다: {e}"

    return {**state, "recommendations": recommendations}
