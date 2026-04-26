import json
import re
from typing import Optional, List # 추가
from pydantic import BaseModel, Field # 추가

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import traceable

from agent.state import AgentState, UserCondition, UserLifestyle
from tools.filter_tool import filter_and_score_raw
from tools.web_search_tool import format_web_context, search_neighborhood

class LifestyleModel(BaseModel):
    activities: List[str] = Field(description="['런닝','자전거','등산','수영','헬스'] 중 언급된 것", default_factory=list)
    atmosphere: Optional[str] = Field(description="'조용한' | '활발한' | '자연친화적' | '카페거리' | '번화가'", default=None)
    amenities: List[str] = Field(description="['공원','한강','카페','헬스장','마트','병원','학교','편의점'] 중 언급된 것", default_factory=list)
    raw_keywords: Optional[str] = Field(description="생활권 원문", default=None)

class UserConditionModel(BaseModel):
    region: Optional[str] = Field(description="지역명", default=None)
    deal_type: Optional[str] = Field(description="'월세' | '전세' | '매매'", default=None)
    max_deposit: Optional[int] = Field(description="보증금 (만원 단위 정수)", default=None)
    max_monthly: Optional[int] = Field(description="월세 (만원 단위 정수)", default=None)
    max_price: Optional[int] = Field(description="매매가 (만원 단위 정수)", default=None)
    min_area: Optional[float] = Field(description="최소 면적 (m²)", default=None)
    property_type: Optional[str] = Field(description="'원룸' | '투룸' | '쓰리룸' | '아파트' | '오피스텔'", default=None)
    min_households: Optional[int] = Field(description="최소 세대수", default=None)
    parking_required: Optional[bool] = Field(description="주차 필수 여부", default=None)
    building_structure: Optional[str] = Field(description="'계단식' | '복도식'", default=None)
    max_subway_minutes: Optional[int] = Field(description="역까지 최대 도보 (분)", default=None)
    min_rooms: Optional[int] = Field(description="최소 방 개수", default=None)
    min_bathrooms: Optional[int] = Field(description="최소 욕실 개수", default=None)
    preferred_floor: Optional[str] = Field(description="'저층' | '중층' | '고층'", default=None)
    direction: Optional[str] = Field(description="'남향' | '동향' | '서향' | '북향'", default=None)
    max_building_age: Optional[int] = Field(description="최대 건물 연식", default=None)
    lifestyle: Optional[LifestyleModel] = None

def _llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0)


# ── parse_condition_node ──────────────────────────────────────────────────────


def _correct_amounts(condition: dict, user_input: str) -> dict:
    """
    LLM이 억 단위 금액을 잘못 계산한 경우 Python으로 교정.

    LLM은 '20억'을 20,000(만원)으로 계산하는 오류를 자주 범함.
    올바른 값: 20억 = 20 × 10,000 = 200,000만원.

    원본 텍스트에서 억 단위를 직접 추출해 파싱된 값과 비교 후 교정.
    """
    eok_nums = re.findall(r'(\d+(?:\.\d+)?)\s*억', user_input)
    if not eok_nums:
        return condition  # 억 단위 없으면 교정 불필요

    # 억 → 만원 변환 목록 (내림차순)
    eok_manwon = sorted([int(float(n) * 10000) for n in eok_nums], reverse=True)

    corrected = dict(condition)
    for field in ("max_deposit", "max_price", "max_monthly"):
        val = corrected.get(field)
        if not val:
            continue
        for correct_val in eok_manwon:
            if correct_val <= 0:
                continue
            ratio = correct_val / val
            # 5배 이상 차이나면 LLM 계산 오류로 판단 → 교정
            if ratio >= 5:
                print(f"[parse 교정] {field}: {val:,}만 → {correct_val:,}만 (억 단위 재계산)")
                corrected[field] = correct_val
                break

    return corrected


@traceable(name="parse_condition")
def _parse_input(user_input: str) -> dict:
    """자연어 → 매물 스펙 조건 + 생활권 조건 동시 추출 (Pydantic 강제 적용)"""
    system_prompt = """사용자의 부동산 검색 조건을 분석하여 추출하세요.
    - 금액은 만원 단위 정수로 변환 (1억→10000)
    - 면적은 m² 단위로 변환 (1평=3.3m²)
    """
    
    # with_structured_output을 사용하여 완벽한 JSON 포맷을 보장받음
    structured_llm = _llm().with_structured_output(UserConditionModel)
    
    response = structured_llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input),
    ])
    
    # Pydantic 객체를 Dict로 변환하여 반환
    return response.model_dump(exclude_none=True)


def parse_condition_node(state: AgentState) -> AgentState:
    user_input = state["user_input"]
    parsed     = {}

    try:
        parsed = _parse_input(user_input)
    except Exception as e:
        print(f"[parse 오류] {e}")

    # None 제거 후 조건 구성
    condition: UserCondition = {
        k: v for k, v in {
            "region":             parsed.get("region"),
            "deal_type":          parsed.get("deal_type"),
            "max_deposit":        parsed.get("max_deposit"),
            "max_monthly":        parsed.get("max_monthly"),
            "max_price":          parsed.get("max_price"),
            "min_area":           parsed.get("min_area"),
            "property_type":      parsed.get("property_type"),
            "min_households":     parsed.get("min_households"),
            "parking_required":   parsed.get("parking_required"),
            "building_structure": parsed.get("building_structure"),
            "max_subway_minutes": parsed.get("max_subway_minutes"),
            "min_rooms":          parsed.get("min_rooms"),
            "min_bathrooms":      parsed.get("min_bathrooms"),
            "preferred_floor":    parsed.get("preferred_floor"),
            "direction":          parsed.get("direction"),
            "max_building_age":   parsed.get("max_building_age"),
        }.items() if v is not None
    }

    raw_ls = parsed.get("lifestyle") or {}
    lifestyle: UserLifestyle = {
        "activities":   raw_ls.get("activities") or [],
        "atmosphere":   raw_ls.get("atmosphere"),
        "amenities":    raw_ls.get("amenities") or [],
        "raw_keywords": raw_ls.get("raw_keywords"),
    }

    # LLM 억 단위 계산 오류 교정
    condition = _correct_amounts(condition, user_input)
    print(f"[parse] 조건={condition}")
    if lifestyle.get("raw_keywords"):
        print(f"[parse] 생활권={lifestyle}")

    return {
        **state,
        "condition":        condition,
        "lifestyle":        lifestyle,
        "clarify_question": None,
        "messages":         list(state.get("messages", [])) + [
            HumanMessage(content=user_input),
            AIMessage(content=str(condition)),
        ],
    }


# ── validate_node ─────────────────────────────────────────────────────────────

def validate_node(state: AgentState) -> AgentState:
    """지역·거래유형·가격 3개 필수. retry 2회 초과 시 강제 통과."""
    condition   = state.get("condition", {})
    retry_count = state.get("retry_count", 0)

    if retry_count >= 2:
        return {**state, "is_valid": True, "error_message": None}

    has_region    = bool(condition.get("region"))
    has_deal_type = bool(condition.get("deal_type"))
    has_price     = bool(
        condition.get("max_deposit") or condition.get("max_monthly") or condition.get("max_price")
    )

    if has_region and has_deal_type and has_price:
        return {**state, "is_valid": True, "error_message": None}

    missing = [k for k, v in {"지역": has_region, "거래유형": has_deal_type, "가격": has_price}.items() if not v]
    return {**state, "is_valid": False, "error_message": f"필수 조건 누락: {', '.join(missing)}"}


# ── clarify_node ──────────────────────────────────────────────────────────────

def clarify_node(state: AgentState) -> AgentState:
    """
    부족한 조건을 LLM으로 질문 생성 → state 저장 → 그래프 종료.
    API가 질문을 프론트에 반환하고, 사용자 답변은 다음 요청에 포함됨.
    """
    condition = state.get("condition", {})
    examples  = {
        "희망 지역":               "예: 서울 마포구, 강남구 역삼동",
        "거래 유형(월세/전세/매매)": "예: 월세, 전세, 매매",
        "예산":                   "예: 보증금 3000/월 80, 전세 3억 이하",
    }
    missing = []
    if not condition.get("region"):    missing.append("희망 지역")
    if not condition.get("deal_type"): missing.append("거래 유형(월세/전세/매매)")
    if not any(condition.get(k) for k in ("max_deposit", "max_monthly", "max_price")):
        missing.append("예산")

    missing_text = "\n".join(f"- {m} ({examples[m]})" for m in missing)

    try:
        response = _llm().invoke([
            SystemMessage(content=(
                "부동산 상담 AI입니다. 부족한 정보를 친근하게 재질문하세요. "
                "사과 표현 금지.\n\n부족한 정보:\n" + missing_text
            )),
            HumanMessage(content=state["user_input"]),
        ])
        question = response.content
    except Exception:
        question = f"아래 정보를 알려주시면 바로 찾아드릴게요!\n{missing_text}"

    print(f"[clarify] {question[:60]}...")

    return {
        **state,
        "clarify_question": question,
        "retry_count":      state.get("retry_count", 0) + 1,
    }


# ── search_and_filter_node ────────────────────────────────────────────────────

@traceable(name="search_and_filter")
def search_and_filter_node(state: AgentState) -> AgentState:
    """
    국토부 실거래가 API로 실제 매물 데이터를 조회합니다.
    데이터가 없으면 LLM 생성 없이 빈 결과를 반환합니다.
    """
    from tools.molit_api import search_real_properties

    condition = state.get("condition", {})
    lifestyle = state.get("lifestyle", {})
    relaxed   = state.get("relaxed", False)

    print(f"\n[search] 실거래 API 조회 (relaxed={relaxed})")

    try:
        search_results = search_real_properties(condition)
    except EnvironmentError as e:
        return {**state, "search_results": [], "filtered_results": [], "error_message": str(e)}
    except Exception as e:
        return {**state, "search_results": [], "filtered_results": [], "error_message": f"API 오류: {e}"}

    if not search_results:
        return {
            **state,
            "search_results":   [],
            "filtered_results": [],
            "error_message":    "해당 조건의 실거래 데이터가 없습니다. 지역이나 거래유형을 변경해보세요.",
        }

    print(f"[search] {len(search_results)}건 수집")

    # relaxed 모드: 소프트 조건 완화
    filter_cond = dict(condition)
    if relaxed:
        for key in ("min_households", "building_structure", "direction", "preferred_floor"):
            filter_cond.pop(key, None)
        print("[search] 소프트 조건 완화 적용")

    filtered_results = filter_and_score_raw(search_results, filter_cond, lifestyle)
    print(f"[search] 필터 통과 {len(filtered_results)}건")

    return {
        **state,
        "search_results":   search_results,
        "filtered_results": filtered_results,
        "error_message":    None,
    }


# ── verify_node ───────────────────────────────────────────────────────────────

def _check_price(prop: dict, condition: dict) -> bool:
    dt      = prop.get("deal_type", "")
    deposit = prop.get("price", {}).get("deposit", 0)
    monthly = prop.get("price", {}).get("monthly", 0)

    if dt == "월세":
        if condition.get("max_deposit") and deposit > condition["max_deposit"]: return False
        if condition.get("max_monthly") and monthly > condition["max_monthly"]: return False
    elif dt == "전세":
        max_d = condition.get("max_deposit") or condition.get("max_price")
        if max_d and deposit > max_d: return False
    elif dt == "매매":
        max_p = condition.get("max_price") or condition.get("max_deposit")
        if max_p and deposit > max_p: return False
    return True


def _check_type(prop: dict, condition: dict) -> bool:
    # deal_type, property_type은 이미 filter에서 처리됨
    # verify에서는 안전망 역할만 수행
    if condition.get("deal_type") and prop.get("deal_type") != condition["deal_type"]:
        return False
    return True


def _check_region(prop: dict, condition: dict) -> bool:
    wanted = condition.get("region")
    if not wanted:
        return True
    # region 필드에는 "마포구 도화동" 형태로 저장됨 (molit_api.py Bug 1 수정)
    # condition의 region이 "마포구"이면 "마포구 도화동"에서 매칭됨
    haystack = " ".join(str(prop.get(k, "")) for k in ("region", "district", "title"))
    # 구 단위 매칭: "마포구" → "마포구" in "마포구 도화동" → True
    return any(w in haystack for w in wanted.split())


@traceable(name="verify")
def verify_node(state: AgentState) -> AgentState:
    """가격·거래유형·지역 3가지 필수 조건을 재검증합니다."""
    condition = state.get("condition", {})
    filtered  = state.get("filtered_results", [])

    verified = [
        p for p in filtered
        if _check_price(p, condition) and _check_type(p, condition) and _check_region(p, condition)
    ]

    print(f"[verify] {len(filtered)}건 → {len(verified)}건 통과")

    new_state = {**state, "filtered_results": verified}
    if not verified:
        retry = state.get("verify_retry_count", 0) + 1
        new_state["verify_retry_count"] = retry
        new_state["relaxed"]            = True
        print(f"[verify] 재시도 {retry}회차 예약")
    return new_state


# ── recommend_node ────────────────────────────────────────────────────────────

@traceable(name="recommend")
def recommend_node(state: AgentState) -> AgentState:
    """실거래 데이터 + 생활권 조건을 반영한 추천 코멘트를 생성합니다."""

    filtered  = state.get("filtered_results", [])
    condition = state.get("condition", {})
    lifestyle = state.get("lifestyle", {})

    # 매물 없음 → 조건 완화 힌트 반환
    if not filtered:
        hints = []
        if any(condition.get(k) for k in ("max_deposit", "max_monthly", "max_price")):
            hints.append("• 금액 상한을 조금 높여보세요")
        if condition.get("property_type"):
            hints.append(f"• '{condition['property_type']}' 외 다른 유형도 고려해보세요")
        hints.append("• 인근 다른 구도 함께 찾아보세요")

        return {
            **state,
            "recommendations": "🔎 조건에 맞는 실거래 데이터가 없어요.\n" + "\n".join(hints),
        }

    # 동네 정보 웹 검색 (Tavily 설정 시)
    region     = condition.get("region", "")
    ls_keyword = lifestyle.get("raw_keywords", "")
    web_info   = search_neighborhood(region, ls_keyword)
    web_ctx    = format_web_context(web_info)

    # 생활권 조건 텍스트
    ls_parts = []
    if lifestyle.get("activities"): ls_parts.append(f"액티비티: {', '.join(lifestyle['activities'])}")
    if lifestyle.get("atmosphere"): ls_parts.append(f"분위기: {lifestyle['atmosphere']}")
    if lifestyle.get("amenities"):  ls_parts.append(f"선호 시설: {', '.join(lifestyle['amenities'])}")
    ls_text = "\n".join(ls_parts) or "없음"

    system_prompt = (
        "당신은 친절한 부동산 추천 전문가입니다.\n"
        "실거래 데이터 기반으로 각 매물의 특징과 추천 이유를 설명하세요.\n"
        "생활권 조건이 있으면 동네 환경과 연결해서 구체적으로 설명해주세요.\n"
        "이모지를 활용해 가독성 있게 작성하고, 1순위 추천 매물을 명시해주세요.\n"
        "⚠️ 이 데이터는 국토교통부 실거래가 기록이므로 현재 매물이 아닐 수 있음을 안내해주세요."
    )

    user_message = (
        f"매물 조건: {json.dumps(condition, ensure_ascii=False)}\n\n"
        f"생활권 조건:\n{ls_text}\n\n"
        f"실거래 데이터:\n{json.dumps(filtered, ensure_ascii=False, indent=2)}"
        + (f"\n\n{web_ctx}" if web_ctx else "")
        + "\n\n위 데이터를 바탕으로 추천 분석을 작성해주세요."
    )

    try:
        response = _llm().invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        recommendations = response.content
    except Exception as e:
        recommendations = f"추천 생성 중 오류가 발생했습니다: {e}"

    return {**state, "recommendations": recommendations}