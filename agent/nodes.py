import json
import re
import sys
from typing import Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from agent.state import AgentState, UserCondition
from tools.filter_tool import filter_and_score
from tools.llm_search_tool import llm_generate_properties


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0)


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
        "- region: 위치를 나타내는 모든 표현 추출\n"
        "  · 구/동 이름: '성동구', '마포구', '강남구', '공덕동' 등\n"
        "  · 지하철역 이름: '서울역', '강남역', '홍대입구역' (뒤에 '역' 붙은 형태 그대로)\n"
        "  · 랜드마크/지역명: '한강', '잠실', '이태원' 등\n"
        "  · 여러 개가 언급되면 가장 구체적인 하나만 선택\n"
        "- deal_type: '월세', '전세', '매매' 중 언급된 것. 둘 다 언급 시 더 명확한 것 선택\n"
        "- 금액은 만원 단위 숫자만 (예: 500만원→500, 3000만원→3000, 1억→10000)\n"
        "- min_households: '세대수 OOO 이상', '대단지(500세대 이상)' 같은 표현에서 숫자 추출\n"
        "- parking_required: '주차 가능', '주차 필수' 등이면 true\n"
        "- max_subway_minutes: '역에서 도보 N분 이내' 같은 표현에서 N 추출\n"
        "- max_building_age: '10년 이내', '신축(5년 이내)' 같은 표현에서 숫자 추출\n"
        "- 명시되지 않은 필드는 반드시 null\n"
        "- JSON 외 다른 텍스트 없이 JSON만 응답"
    )
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input),
    ])
    text = response.content.strip()
    # 코드블록 제거
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
        "region": parsed.get("region") or None,
        "deal_type": parsed.get("deal_type") or None,
        "max_deposit": parsed.get("max_deposit") or None,
        "max_monthly": parsed.get("max_monthly") or None,
        "max_price": parsed.get("max_price") or None,
        "min_area": parsed.get("min_area") or None,
        "property_type": parsed.get("property_type") or None,
        "min_households": parsed.get("min_households") or None,
        "parking_required": parsed.get("parking_required"),
        "building_structure": parsed.get("building_structure") or None,
        "max_subway_minutes": parsed.get("max_subway_minutes") or None,
        "min_rooms": parsed.get("min_rooms") or None,
        "min_bathrooms": parsed.get("min_bathrooms") or None,
        "preferred_floor": parsed.get("preferred_floor") or None,
        "direction": parsed.get("direction") or None,
        "max_building_age": parsed.get("max_building_age") or None,
    }

    messages = list(state.get("messages", [])) + [
        HumanMessage(content=user_input),
        AIMessage(content=str(condition)),
    ]

    return {**state, "condition": condition, "messages": messages}


def validate_node(state: AgentState) -> AgentState:
    """조건의 유효성을 검증합니다."""
    condition = state.get("condition", {})
    retry_count = state.get("retry_count", 0)

    # retry_count >= 2이면 강제 통과
    if retry_count >= 2:
        return {**state, "is_valid": True, "error_message": None}

    has_region = bool(condition.get("region"))
    has_deal_type = bool(condition.get("deal_type"))
    has_price = bool(
        condition.get("max_deposit")
        or condition.get("max_monthly")
        or condition.get("max_price")
    )

    # 지역·유형·가격 세 조건 모두 있어야 검색 가능
    if has_region and has_deal_type and has_price:
        return {**state, "is_valid": True, "error_message": None}

    missing = []
    if not has_region:
        missing.append("지역")
    if not has_deal_type:
        missing.append("거래유형")
    if not has_price:
        missing.append("가격")
    return {
        **state,
        "is_valid": False,
        "error_message": f"필수 조건 누락: {', '.join(missing)}",
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
    if not (
        condition.get("max_deposit")
        or condition.get("max_monthly")
        or condition.get("max_price")
    ):
        missing.append("예산(가격)")

    examples = {
        "희망 지역": "예: '서울 마포구', '강남구 역삼동', '송파구'",
        "거래 유형(월세/전세/매매)": "예: '월세', '전세', '매매'",
        "예산(가격)": "예: '보증금 3000만/월 80만 이하', '전세 5억 이하', '매매 20억 이하'",
    }
    missing_with_examples = "\n".join(
        f"- {m} ({examples.get(m, '')})" for m in missing
    )

    system_prompt = (
        "당신은 부동산 상담 AI입니다. 사용자의 질문이 조금 더 구체적이면 "
        "더 정확한 매물을 찾아드릴 수 있습니다.\n"
        "아래 부족한 정보에 대해 **사과 없이**, 사용자가 바로 답할 수 있도록 "
        "예시와 선택지를 곁들여 한 번에 재질문해주세요.\n"
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

    print(f"\n🤔 추가 정보가 필요해요: {question}")
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


def _format_price(prop: dict) -> str:
    deal_type = prop.get("deal_type", "")
    price = prop.get("price", {})
    deposit = price.get("deposit", 0)
    monthly = price.get("monthly", 0)
    if deal_type == "월세":
        return f"보증금 {deposit}만원 / 월세 {monthly}만원"
    if deal_type == "전세":
        return f"전세 {deposit}만원"
    if deal_type == "매매":
        return f"매매 {deposit}만원"
    return f"{deposit}/{monthly}"


def _log_property_details(prop: dict) -> None:
    """매물 상세 정보를 요청된 항목 형식으로 로그 출력."""
    title = prop.get("title", "-")
    print(f"  🏠 [{prop.get('id', '-')}] {title}")
    print(f"     - 위치: {prop.get('region', '-')} {prop.get('district', '-')}")
    print(f"     - 금액대: {_format_price(prop)}")
    print(f"     - 면적: {prop.get('area_m2', '-')}m²")
    print(f"     - 세대수: {prop.get('households', '-')}세대")
    print(f"     - 주차여부: {'가능' if prop.get('parking') else '불가'}")
    print(f"     - 계단식/복도식: {prop.get('building_structure', '-')}")
    print(f"     - 근처 역까지: {prop.get('subway_minutes', '-')}분 ({prop.get('subway', '-')})")
    print(f"     - 방/욕실: {prop.get('rooms', '-')}개 / {prop.get('bathrooms', '-')}개")
    print(
        f"     - 층/방향: {prop.get('floor', '-')}층"
        f"(총 {prop.get('total_floors', '-')}층) / {prop.get('direction', '-')}"
    )
    print(f"     - 연식: {prop.get('built_year', '-')}년식")


def search_and_filter_node(state: AgentState) -> AgentState:
    """LLM이 조건에 맞는 매물을 생성하고, 그중 적합도 순으로 필터링합니다."""
    condition = state.get("condition", {})

    print("\n[🔎 검색 조건]")
    for key, label in [
        ("region", "위치"),
        ("deal_type", "거래유형"),
        ("property_type", "방 종류"),
        ("max_deposit", "최대 보증금(만원)"),
        ("max_monthly", "최대 월세(만원)"),
        ("max_price", "최대 가격(만원)"),
        ("min_area", "최소 면적(m²)"),
        ("min_households", "최소 세대수"),
        ("parking_required", "주차 필수 여부"),
        ("building_structure", "계단식/복도식"),
        ("max_subway_minutes", "역까지 최대 도보(분)"),
        ("min_rooms", "최소 방 개수"),
        ("min_bathrooms", "최소 욕실 개수"),
        ("preferred_floor", "선호 층"),
        ("direction", "선호 방향"),
        ("max_building_age", "최대 건물 연식(년)"),
    ]:
        val = condition.get(key)
        if val is not None:
            print(f"  - {label}: {val}")

    print("\n[🤖 Agent가 조건에 맞는 매물 생성 중...]")
    search_results = llm_generate_properties(condition, count=6)

    print(f"\n[📋 Agent 생성 매물: {len(search_results)}건]")
    for prop in search_results:
        _log_property_details(prop)

    filtered_results = filter_and_score.invoke({"properties": search_results, "condition": condition})

    print(f"\n[✅ 필터링 결과: {len(filtered_results)}건]")
    for prop in filtered_results:
        _log_property_details(prop)
        print(f"     - 매칭점수: {prop.get('score', 0)}점")

    return {
        **state,
        "search_results": search_results,
        "filtered_results": filtered_results,
    }


def _verify_price(prop: dict, condition: dict):
    deal_type = prop.get("deal_type", "")
    price = prop.get("price", {})
    deposit = price.get("deposit", 0)
    monthly = price.get("monthly", 0)

    if deal_type == "월세":
        max_deposit = condition.get("max_deposit")
        max_monthly = condition.get("max_monthly")
        if max_deposit and deposit > max_deposit:
            return False, f"보증금 {deposit} > 최대 {max_deposit}"
        if max_monthly and monthly > max_monthly:
            return False, f"월세 {monthly} > 최대 {max_monthly}"
        return True, f"보증금 {deposit}/월 {monthly} ≤ 조건"
    if deal_type == "전세":
        max_deposit = condition.get("max_deposit") or condition.get("max_price")
        if max_deposit and deposit > max_deposit:
            return False, f"전세가 {deposit} > 최대 {max_deposit}"
        return True, f"전세가 {deposit} ≤ 조건"
    if deal_type == "매매":
        max_price = condition.get("max_price") or condition.get("max_deposit")
        if max_price and deposit > max_price:
            return False, f"매매가 {deposit} > 최대 {max_price}"
        return True, f"매매가 {deposit} ≤ 조건"
    return False, f"알 수 없는 거래유형: {deal_type}"


def _verify_type(prop: dict, condition: dict):
    reasons = []
    wanted_deal = condition.get("deal_type")
    wanted_prop = condition.get("property_type")

    if wanted_deal and prop.get("deal_type") != wanted_deal:
        return False, f"거래유형 {prop.get('deal_type')} ≠ {wanted_deal}"
    if wanted_deal:
        reasons.append(f"거래유형 {wanted_deal} 일치")

    if wanted_prop and prop.get("type") != wanted_prop:
        return False, f"방 유형 {prop.get('type')} ≠ {wanted_prop}"
    if wanted_prop:
        reasons.append(f"방 유형 {wanted_prop} 일치")

    if not reasons:
        return True, "유형 조건 미지정 → 통과"
    return True, ", ".join(reasons)


def _verify_region(prop: dict, condition: dict):
    wanted = condition.get("region")
    if not wanted:
        return True, "지역 조건 미지정 → 통과"

    haystack = " ".join(
        str(prop.get(k, "")) for k in ("region", "district", "subway", "title")
    )
    if wanted in haystack:
        return True, f"'{wanted}' 매칭"
    return False, f"'{wanted}' 불일치 (매물 지역: {prop.get('region')} {prop.get('district')})"


def verify_node(state: AgentState) -> AgentState:
    """추천 매물이 사용자의 필수 조건(가격/유형/지역)에 부합하는지 점검합니다."""
    condition = state.get("condition", {})
    filtered = state.get("filtered_results", [])

    print("\n[🛡️ 필수조건 검증: 가격 / 유형 / 지역]")

    verified = []
    for prop in filtered:
        price_ok, price_reason = _verify_price(prop, condition)
        type_ok, type_reason = _verify_type(prop, condition)
        region_ok, region_reason = _verify_region(prop, condition)
        all_pass = price_ok and type_ok and region_ok

        status = "✅ 통과" if all_pass else "❌ 탈락"
        print(f"  {status} [{prop.get('id', '-')}] {prop.get('title', '-')}")
        print(f"     - 가격: {'✅' if price_ok else '❌'} {price_reason}")
        print(f"     - 유형: {'✅' if type_ok else '❌'} {type_reason}")
        print(f"     - 지역: {'✅' if region_ok else '❌'} {region_reason}")

        if all_pass:
            verified.append(prop)

    print(f"\n[📌 검증 결과] {len(filtered)}건 중 {len(verified)}건 통과")

    new_state = {**state, "filtered_results": verified}
    if not verified:
        retry = state.get("verify_retry_count", 0) + 1
        new_state["verify_retry_count"] = retry
        print(f"[♻️ 통과 매물 0건 → 재검색 시도 {retry}회차]")
    return new_state


def recommend_node(state: AgentState) -> AgentState:
    """필터링된 매물을 바탕으로 자연어 추천 텍스트를 생성합니다."""
    llm = _get_llm()
    filtered = state.get("filtered_results", [])
    condition = state.get("condition", {})

    if not filtered:
        hints = []
        if condition.get("max_deposit") or condition.get("max_monthly") or condition.get("max_price"):
            hints.append("• 금액 상한을 조금 더 여유 있게 잡아보시겠어요? (예: 보증금/월세 +20%)")
        if condition.get("min_area"):
            hints.append("• 최소 면적 기준을 낮춰보세요 (예: 30m² 이상 → 20m² 이상)")
        if condition.get("property_type"):
            hints.append(f"• 방 종류를 '{condition['property_type']}' 외 다른 유형도 함께 고려해보세요")
        if condition.get("region"):
            hints.append(f"• '{condition['region']}' 인근 지역도 함께 찾아드릴까요?")
        if not hints:
            hints.append("• 희망 지역·거래유형·금액 중 한 가지를 조정해 다시 알려주세요")

        recommendations = (
            "🔎 조건을 조금만 조정해주시면 바로 다시 찾아드릴 수 있어요!\n"
            + "\n".join(hints)
            + "\n\n어떤 기준을 바꿔보시겠어요?"
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
