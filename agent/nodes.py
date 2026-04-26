import json
import re
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langsmith import traceable

from agent.state import AgentState, UserCondition
from tools.filter_tool import filter_and_score_raw
from tools.llm_search_tool import llm_generate_properties


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0)


# ── parse ─────────────────────────────────────────────────────────────────────

@traceable(name="parse_condition")
def _parse_with_json(llm: ChatOpenAI, user_input: str) -> dict:
    """JSON 응답 방식으로 조건 파싱."""
    system_prompt = (
        "사용자의 부동산 검색 조건을 분석해서 아래 JSON 형식으로만 응답하세요.\n"
        "{\n"
        '  "region": "지역명 또는 null",\n'
        '  "deal_type": "월세 또는 전세 또는 매매 또는 null",\n'
        '  "max_deposit": 숫자 또는 null,\n'
        '  "max_monthly": 숫자 또는 null,\n'
        '  "max_price": 숫자 또는 null,\n'
        '  "min_area": 숫자 또는 null,\n'
        '  "property_type": "원룸 또는 투룸 또는 쓰리룸 또는 아파트 또는 오피스텔 또는 null",\n'
        '  "min_households": 숫자 또는 null,\n'
        '  "parking_required": true/false 또는 null,\n'
        '  "building_structure": "계단식 또는 복도식 또는 null",\n'
        '  "max_subway_minutes": 숫자 또는 null,\n'
        '  "min_rooms": 숫자 또는 null,\n'
        '  "min_bathrooms": 숫자 또는 null,\n'
        '  "preferred_floor": "저층 또는 중층 또는 고층 또는 null",\n'
        '  "direction": "남향 또는 동향 또는 서향 또는 북향 또는 남동향 또는 남서향 또는 null",\n'
        '  "max_building_age": 숫자 또는 null\n'
        "}\n\n"
        "규칙:\n"
        "- region: 구/동 이름, 지하철역 이름, 랜드마크. 여러 개면 가장 구체적인 하나만 선택\n"
        "- deal_type: '월세', '전세', '매매' 중 언급된 것\n"
        "- 금액은 만원 단위 숫자만 (예: 500만원→500, 1억→10000)\n"
        "- parking_required: '주차 가능', '주차 필수' 등이면 true\n"
        "- max_subway_minutes: '역에서 도보 N분 이내' 표현에서 N 추출\n"
        "- max_building_age: '10년 이내', '신축(5년 이내)' 표현에서 숫자 추출\n"
        "- 명시되지 않은 필드는 반드시 null\n"
        "- JSON 외 다른 텍스트 없이 JSON만 응답"
    )
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input),
    ])
    text = response.content.strip()
    text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    return json.loads(text)


def parse_condition_node(state: AgentState) -> AgentState:
    """사용자 자연어 입력을 파싱하여 UserCondition을 추출합니다."""
    llm = _get_llm()
    user_input = state["user_input"]

    parsed = {}
    try:
        parsed = _parse_with_json(llm, user_input)
    except Exception as e:
        print(f"[parse 오류] {e}")

    condition: UserCondition = {
        "region":            parsed.get("region") or None,
        "deal_type":         parsed.get("deal_type") or None,
        "max_deposit":       parsed.get("max_deposit") or None,
        "max_monthly":       parsed.get("max_monthly") or None,
        "max_price":         parsed.get("max_price") or None,
        "min_area":          parsed.get("min_area") or None,
        "property_type":     parsed.get("property_type") or None,
        "min_households":    parsed.get("min_households") or None,
        "parking_required":  parsed.get("parking_required"),
        "building_structure":parsed.get("building_structure") or None,
        "max_subway_minutes":parsed.get("max_subway_minutes") or None,
        "min_rooms":         parsed.get("min_rooms") or None,
        "min_bathrooms":     parsed.get("min_bathrooms") or None,
        "preferred_floor":   parsed.get("preferred_floor") or None,
        "direction":         parsed.get("direction") or None,
        "max_building_age":  parsed.get("max_building_age") or None,
    }

    messages = list(state.get("messages", [])) + [
        HumanMessage(content=user_input),
        AIMessage(content=str(condition)),
    ]

    return {
        **state,
        "condition": condition,
        "clarify_question": None,   # parse 진입 시 초기화
        "messages": messages,
    }


# ── validate ──────────────────────────────────────────────────────────────────

def validate_node(state: AgentState) -> AgentState:
    """조건의 유효성을 검증합니다."""
    condition = state.get("condition", {})
    retry_count = state.get("retry_count", 0)

    if retry_count >= 2:
        return {**state, "is_valid": True, "error_message": None}

    has_region    = bool(condition.get("region"))
    has_deal_type = bool(condition.get("deal_type"))
    has_price     = bool(
        condition.get("max_deposit")
        or condition.get("max_monthly")
        or condition.get("max_price")
    )

    if has_region and has_deal_type and has_price:
        return {**state, "is_valid": True, "error_message": None}

    missing = []
    if not has_region:    missing.append("지역")
    if not has_deal_type: missing.append("거래유형")
    if not has_price:     missing.append("가격")

    return {
        **state,
        "is_valid": False,
        "error_message": f"필수 조건 누락: {', '.join(missing)}",
    }


# ── clarify ───────────────────────────────────────────────────────────────────

def clarify_node(state: AgentState) -> AgentState:
    """
    부족한 조건을 사용자에게 질문합니다.

    stdin을 사용하지 않습니다.
    clarify_question에 질문을 저장하면 graph가 END로 빠져나가고,
    API가 프론트엔드에 질문을 반환합니다.
    사용자가 답변하면 clarify_answer를 포함한 새 요청이 들어와
    combined_input으로 재파싱됩니다.
    """
    llm = _get_llm()
    condition = state.get("condition", {})

    missing = []
    if not condition.get("region"):    missing.append("희망 지역")
    if not condition.get("deal_type"): missing.append("거래 유형(월세/전세/매매)")
    if not (condition.get("max_deposit") or condition.get("max_monthly") or condition.get("max_price")):
        missing.append("예산(가격)")

    examples = {
        "희망 지역":               "예: '서울 마포구', '강남구 역삼동', '송파구'",
        "거래 유형(월세/전세/매매)": "예: '월세', '전세', '매매'",
        "예산(가격)":              "예: '보증금 3000만/월 80만 이하', '전세 5억 이하', '매매 20억 이하'",
    }
    missing_with_examples = "\n".join(
        f"- {m} ({examples.get(m, '')})" for m in missing
    )

    system_prompt = (
        "당신은 부동산 상담 AI입니다. 사용자에게 부족한 정보를 물어보세요.\n"
        "'죄송합니다', '찾을 수 없습니다' 같은 사과/부정 표현은 절대 쓰지 마세요.\n"
        f"부족한 정보:\n{missing_with_examples}\n\n"
        "출력 형식: 한두 문장으로 친근하게 재질문 + 각 항목별 예시를 간단히 제시."
    )

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_input"]),
        ])
        question = response.content
    except Exception:
        question = (
            "조금 더 구체적으로 알려주시면 딱 맞는 매물을 찾아드릴게요!\n"
            f"{missing_with_examples}"
        )

    print(f"[clarify] 질문 생성: {question[:60]}...")

    return {
        **state,
        "clarify_question": question,
        "retry_count": state.get("retry_count", 0) + 1,
    }


# ── search_and_filter ─────────────────────────────────────────────────────────

def _log_property(prop: dict) -> None:
    print(f"  [{prop.get('id','-')}] {prop.get('title','-')} | "
          f"{prop.get('region','')} | score={prop.get('score',0)}")


@traceable(name="search_and_filter")
def search_and_filter_node(state: AgentState) -> AgentState:
    """
    LLM으로 조건에 맞는 매물을 생성하고 필터링합니다.
    verify 재시도(relaxed=True)면 LLM에 조건 완화 신호를 줍니다.
    """
    condition = state.get("condition", {})
    relaxed   = state.get("relaxed", False)
    count     = 8 if relaxed else 6   # 재시도 시 더 많이 생성

    print(f"\n[검색] relaxed={relaxed}, count={count}")

    try:
        search_results = llm_generate_properties(condition, count=count, relaxed=relaxed)
    except Exception as e:
        error_msg = f"매물 생성 중 오류가 발생했습니다: {e}"
        print(f"[LLM 오류] {error_msg}")
        return {
            **state,
            "search_results": [],
            "filtered_results": [],
            "error_message": error_msg,
        }

    if not search_results:
        return {
            **state,
            "search_results": [],
            "filtered_results": [],
            "error_message": "LLM이 조건에 맞는 매물을 생성하지 못했습니다.",
        }

    print(f"[생성] {len(search_results)}건")
    for p in search_results:
        _log_property(p)

    # 조건 완화 모드: 일부 소프트 조건(주차, 세대수 등)을 제거하고 필터링
    filter_condition = dict(condition)
    if relaxed:
        for soft_key in ("min_households", "building_structure", "direction", "preferred_floor"):
            filter_condition.pop(soft_key, None)
        print("[완화] 소프트 조건(세대수/구조/방향/층) 제거 후 필터링")

    filtered_results = filter_and_score_raw(search_results, filter_condition)

    print(f"[필터] {len(filtered_results)}건 통과")
    for p in filtered_results:
        _log_property(p)

    return {
        **state,
        "search_results": search_results,
        "filtered_results": filtered_results,
        "error_message": None,
    }


# ── verify ────────────────────────────────────────────────────────────────────

def _verify_price(prop: dict, condition: dict):
    deal_type = prop.get("deal_type", "")
    price     = prop.get("price", {})
    deposit   = price.get("deposit", 0)
    monthly   = price.get("monthly", 0)

    if deal_type == "월세":
        max_d = condition.get("max_deposit")
        max_m = condition.get("max_monthly")
        if max_d and deposit > max_d:
            return False, f"보증금 {deposit} > 최대 {max_d}"
        if max_m and monthly > max_m:
            return False, f"월세 {monthly} > 최대 {max_m}"
        return True, f"보증금 {deposit}/월 {monthly} ≤ 조건"
    if deal_type == "전세":
        max_d = condition.get("max_deposit") or condition.get("max_price")
        if max_d and deposit > max_d:
            return False, f"전세가 {deposit} > 최대 {max_d}"
        return True, f"전세가 {deposit} ≤ 조건"
    if deal_type == "매매":
        max_p = condition.get("max_price") or condition.get("max_deposit")
        if max_p and deposit > max_p:
            return False, f"매매가 {deposit} > 최대 {max_p}"
        return True, f"매매가 {deposit} ≤ 조건"
    return False, f"알 수 없는 거래유형: {deal_type}"


def _verify_type(prop: dict, condition: dict):
    wanted_deal = condition.get("deal_type")
    wanted_prop = condition.get("property_type")

    if wanted_deal and prop.get("deal_type") != wanted_deal:
        return False, f"거래유형 {prop.get('deal_type')} ≠ {wanted_deal}"
    if wanted_prop and prop.get("type") != wanted_prop:
        return False, f"방 유형 {prop.get('type')} ≠ {wanted_prop}"
    return True, "유형 조건 일치"


def _verify_region(prop: dict, condition: dict):
    wanted = condition.get("region")
    if not wanted:
        return True, "지역 조건 미지정 → 통과"
    haystack = " ".join(str(prop.get(k, "")) for k in ("region", "district", "subway", "title"))
    if wanted in haystack:
        return True, f"'{wanted}' 매칭"
    return False, f"'{wanted}' 불일치 (매물: {prop.get('region')} {prop.get('district')})"


@traceable(name="verify")
def verify_node(state: AgentState) -> AgentState:
    """추천 매물이 필수 조건(가격/유형/지역)에 부합하는지 점검합니다."""
    condition = state.get("condition", {})
    filtered  = state.get("filtered_results", [])

    print("\n[검증] 가격 / 유형 / 지역")

    verified = []
    for prop in filtered:
        price_ok,  price_reason  = _verify_price(prop, condition)
        type_ok,   type_reason   = _verify_type(prop, condition)
        region_ok, region_reason = _verify_region(prop, condition)
        all_pass = price_ok and type_ok and region_ok

        status = "✅" if all_pass else "❌"
        print(f"  {status} [{prop.get('id','-')}] {prop.get('title','-')}")
        if not all_pass:
            print(f"     가격:{price_reason} / 유형:{type_reason} / 지역:{region_reason}")

        if all_pass:
            verified.append(prop)

    print(f"[검증 결과] {len(filtered)}건 중 {len(verified)}건 통과")

    new_state = {**state, "filtered_results": verified}

    if not verified:
        retry = state.get("verify_retry_count", 0) + 1
        new_state["verify_retry_count"] = retry
        new_state["relaxed"] = True   # 다음 search에서 조건 완화
        print(f"[재시도 {retry}회차] relaxed=True 설정")

    return new_state


# ── recommend ─────────────────────────────────────────────────────────────────

@traceable(name="recommend")
def recommend_node(state: AgentState) -> AgentState:
    """필터링된 매물을 바탕으로 자연어 추천 텍스트를 생성합니다."""
    llm      = _get_llm()
    filtered = state.get("filtered_results", [])
    condition = state.get("condition", {})

    if not filtered:
        hints = []
        if condition.get("max_deposit") or condition.get("max_monthly") or condition.get("max_price"):
            hints.append("• 금액 상한을 조금 더 여유 있게 잡아보세요 (예: +20%)")
        if condition.get("min_area"):
            hints.append("• 최소 면적 기준을 낮춰보세요")
        if condition.get("property_type"):
            hints.append(f"• '{condition['property_type']}' 외 다른 유형도 고려해보세요")
        if condition.get("region"):
            hints.append(f"• '{condition['region']}' 인근 지역도 함께 찾아드릴까요?")
        if not hints:
            hints.append("• 희망 지역·거래유형·금액 중 한 가지를 조정해 다시 알려주세요")

        return {
            **state,
            "recommendations": (
                "🔎 조건에 맞는 매물을 찾지 못했습니다. 아래 방법을 시도해보세요!\n"
                + "\n".join(hints)
            ),
        }

    system_prompt = (
        "당신은 친절한 부동산 추천 전문가입니다. "
        "아래 매물 목록을 바탕으로 각 매물의 장단점과 추천 이유를 포함하고, "
        "1순위 추천 매물을 명시해주세요. 이모지로 가독성 있게 작성하세요."
    )
    user_message = (
        f"사용자 조건: {json.dumps(condition, ensure_ascii=False)}\n\n"
        f"추천 매물:\n{json.dumps(filtered, ensure_ascii=False, indent=2)}\n\n"
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