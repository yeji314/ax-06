import json
import re
import warnings
from typing import Optional, List # 추가
from pydantic import BaseModel, Field # 추가

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import traceable

# Pydantic + langchain with_structured_output 조합에서 발생하는 무해한 경고 억제
warnings.filterwarnings(
    "ignore",
    message=r".*PydanticSerializationUnexpectedValue.*",
)

from agent.state import AgentState, UserCondition, UserLifestyle
from tools.filter_tool import filter_and_score_raw
from tools.web_search_tool import format_web_context, search_neighborhood

class LifestyleModel(BaseModel):
    activities: List[str] = Field(description="['런닝','자전거','등산','수영','헬스'] 중 언급된 것", default_factory=list)
    atmosphere: Optional[str] = Field(description="'조용한' | '활발한' | '자연친화적' | '카페거리' | '번화가'", default=None)
    amenities: List[str] = Field(description="['공원','한강','카페','헬스장','마트','병원','학교','편의점'] 중 언급된 것", default_factory=list)
    raw_keywords: Optional[str] = Field(description="생활권 원문", default=None)

class UserConditionModel(BaseModel):
    region: Optional[str] = Field(
        description=(
            "지역명. 다음 형태를 모두 인정합니다: "
            "구/시 이름(예: 마포구, 강남구), 동 이름(예: 공덕동), "
            "지하철역 이름(예: 서울역, 강남역, 홍대), "
            "지하철 노선 이름(예: 2호선, 7호선, 분당선, 신분당선), "
            "랜드마크(예: 한강, 잠실)"
        ),
        default=None,
    )
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
    top_floor_only: Optional[bool] = Field(
        description="사용자가 '탑층' 또는 '꼭대기 층'을 명시했으면 True",
        default=None,
    )
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

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        response = structured_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_input),
        ])

    # Pydantic 객체를 Dict로 변환하여 반환
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return response.model_dump(exclude_none=True)


_VALID_PROPERTY_TYPES = {"원룸", "투룸", "쓰리룸", "아파트", "오피스텔"}
_VALID_DEAL_TYPES     = {"월세", "전세", "매매"}


def _sanitize_input(text: str) -> str:
    """
    사용자 입력의 일반적인 오타·IME 잔재 정리.
    - 'D2호선' → '2호선' : 한글/영문 모드 혼선으로 영문 한 글자가 호선 앞에 붙는 경우
    - 'D2 호선' → '2호선' : 공백 포함
    """
    cleaned = re.sub(r"(?<![A-Za-z])[A-Za-z]\s*(?=\d+\s*호선)", "", text)
    if cleaned != text:
        print(f"[parse 정제] '{text}' → '{cleaned}'")
    return cleaned


# 긴 패턴부터 매칭하고 매칭된 부분은 제거 → 중복 추출 방지 (신분당선이 분당선과 둘 다 잡히는 문제)
_SUBWAY_LINE_PATTERNS = [
    r"수인분당선", r"신분당선",
    r"경의중앙선", r"공항철도", r"우이신설선", r"신림선",
    r"GTX[-\s]?[A-D]",
    r"분당선",
    r"\d+\s*호선",
]


def _extract_subway_lines(text: str) -> list[str]:
    """텍스트에서 '2호선', '분당선' 등 지하철 노선명을 추출 (긴 패턴 우선)."""
    if not text:
        return []
    remaining = text
    found: list[str] = []
    for pattern in _SUBWAY_LINE_PATTERNS:
        for m in re.findall(pattern, remaining):
            normalized = re.sub(r"\s+", "", m)  # "2 호선" → "2호선"
            if normalized not in found:
                found.append(normalized)
        # 매칭된 부분 공백으로 치환 → 다음 짧은 패턴이 같은 위치에 다시 매칭되지 않도록
        remaining = re.sub(pattern, " ", remaining)
    return found


def parse_condition_node(state: AgentState) -> AgentState:
    user_input = _sanitize_input(state["user_input"])
    parsed     = {}

    try:
        parsed = _parse_input(user_input)
    except Exception as e:
        print(f"[parse 오류] {e}")

    # 화이트리스트 필터링: LLM이 임의 문자열을 만들어내는 경우 방어
    pt = parsed.get("property_type")
    if pt and pt not in _VALID_PROPERTY_TYPES:
        print(f"[parse 경고] 잘못된 property_type='{pt}' → 무시")
        parsed["property_type"] = None

    dt = parsed.get("deal_type")
    if dt and dt not in _VALID_DEAL_TYPES:
        print(f"[parse 경고] 잘못된 deal_type='{dt}' → 무시")
        parsed["deal_type"] = None

    # 사용자가 명시적으로 말한 경우에만 인정 (LLM이 가격·문맥에서 추론하는 것 차단)
    # 예: '15억' → LLM이 매매로 추론 → 텍스트에 '매매'·'사다'·'전세'·'월세' 없으면 거부
    if parsed.get("deal_type") and not any(dt in user_input for dt in _VALID_DEAL_TYPES):
        print(f"[parse] deal_type='{parsed['deal_type']}' 텍스트 미명시 → 추론 거부")
        parsed["deal_type"] = None
    if parsed.get("property_type") and not any(p in user_input for p in _VALID_PROPERTY_TYPES):
        print(f"[parse] property_type='{parsed['property_type']}' 텍스트 미명시 → 추론 거부")
        parsed["property_type"] = None

    # 새로 파싱된 필드 (None 제거)
    new_fields = {
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
            "top_floor_only":     parsed.get("top_floor_only"),
            "direction":          parsed.get("direction"),
            "max_building_age":   parsed.get("max_building_age"),
        }.items() if v is not None
    }

    # 탑층/꼭대기 층 키워드는 LLM이 종종 놓침 → 정규식으로 보강
    if re.search(r"탑\s*층|꼭대기|맨\s*위층|최상층", user_input):
        new_fields["top_floor_only"] = True

    # 멀티턴: 이전 condition과 병합 (이전 정보 유지 + 새 정보로 덮어쓰기)
    prior_condition = dict(state.get("condition") or {})
    condition: UserCondition = {**prior_condition, **new_fields}

    # lifestyle도 병합 (리스트는 합집합, 스칼라는 새 값 우선)
    prior_ls = dict(state.get("lifestyle") or {})
    raw_ls   = parsed.get("lifestyle") or {}
    new_acts  = raw_ls.get("activities") or []
    new_amens = raw_ls.get("amenities") or []
    lifestyle: UserLifestyle = {
        "activities":   list(dict.fromkeys([*(prior_ls.get("activities") or []), *new_acts])),
        "amenities":    list(dict.fromkeys([*(prior_ls.get("amenities") or []), *new_amens])),
        "atmosphere":   raw_ls.get("atmosphere")   or prior_ls.get("atmosphere"),
        "raw_keywords": raw_ls.get("raw_keywords") or prior_ls.get("raw_keywords"),
    }

    # 지하철 노선이 입력에 있으면 region에 보강 (LLM이 놓치는 경우 안전망)
    subway_lines = _extract_subway_lines(user_input)
    if subway_lines:
        existing_region = condition.get("region", "") or ""
        # region에 노선이 없거나 region이 비어있으면 추가
        missing_lines = [ln for ln in subway_lines if ln not in existing_region]
        if missing_lines:
            condition["region"] = (
                " ".join([existing_region, *missing_lines]).strip()
                if existing_region else " ".join(missing_lines)
            )
            print(f"[parse 보강] 노선 인식 → region='{condition['region']}'")

    # LLM 억 단위 계산 오류 교정
    condition = _correct_amounts(condition, user_input)
    print(f"[parse] 조건={condition}")
    if lifestyle.get("raw_keywords"):
        print(f"[parse] 생활권={lifestyle}")

    return {
        **state,
        "user_input":       user_input,  # 정제된 입력으로 교체 (clarify가 그대로 인용 방지)
        "condition":        condition,
        "lifestyle":        lifestyle,
        "clarify_question": None,
        # 새 입력마다 verify 재시도 상태 리셋 (이전 검색의 잔재 차단)
        "verify_retry_count": 0,
        "relaxed":            False,
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

    has_region        = bool(condition.get("region"))
    has_deal_type     = bool(condition.get("deal_type"))
    has_property_type = bool(condition.get("property_type"))
    has_price         = bool(
        condition.get("max_deposit") or condition.get("max_monthly") or condition.get("max_price")
    )

    if has_region and has_deal_type and has_property_type and has_price:
        return {**state, "is_valid": True, "error_message": None}

    missing = [
        k for k, v in {
            "지역":     has_region,
            "거래유형": has_deal_type,
            "방종류":   has_property_type,
            "가격":     has_price,
        }.items() if not v
    ]
    return {**state, "is_valid": False, "error_message": f"필수 조건 누락: {', '.join(missing)}"}


# ── clarify_node ──────────────────────────────────────────────────────────────

def clarify_node(state: AgentState) -> AgentState:
    """
    부족한 조건을 LLM으로 질문 생성 → state 저장 → 그래프 종료.
    API가 질문을 프론트에 반환하고, 사용자 답변은 다음 요청에 포함됨.
    """
    condition = state.get("condition", {})
    examples  = {
        "희망 지역":                       "예: 서울 마포구, 강남구 역삼동",
        "거래 유형(월세/전세/매매)":         "예: 월세, 전세, 매매",
        "방 종류(아파트/원룸/투룸/오피스텔/빌라)": "예: 아파트, 원룸, 오피스텔",
        "예산":                           "예: 보증금 3000/월 80, 전세 3억 이하, 매매 15억",
    }
    missing = []
    if not condition.get("region"):        missing.append("희망 지역")
    if not condition.get("deal_type"):     missing.append("거래 유형(월세/전세/매매)")
    if not condition.get("property_type"): missing.append("방 종류(아파트/원룸/투룸/오피스텔/빌라)")
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
    verify 재시도 시 인접 구로 검색 범위를 점진 확장합니다.
    """
    from tools.molit_api import (
        NEIGHBOR_GU, get_base_gu, infer_gus_from_lifestyle,
        infer_gus_from_subway_line, is_broad_region,
        search_real_properties_expanded,
    )

    condition = dict(state.get("condition", {}) or {})
    lifestyle = state.get("lifestyle", {}) or {}
    relaxed   = state.get("relaxed", False)
    verify_retry = state.get("verify_retry_count", 0)
    user_input = state.get("user_input", "") or ""

    # 1) region 자체에서 지하철 노선 감지 (예: "2호선 근처")
    region = condition.get("region", "") or ""
    line_gus = infer_gus_from_subway_line(region) or infer_gus_from_subway_line(user_input)

    if line_gus and (is_broad_region(region) or get_base_gu(region) is None):
        condition["region"] = " ".join(line_gus)
        print(f"[search] 지하철 노선 인식 → 대표 구 {line_gus} 로 검색")
    elif is_broad_region(region):
        # 2) 광역 region("서울"·"경기" 등)이면 라이프스타일로 대표 구 추천
        candidates = infer_gus_from_lifestyle(lifestyle, max_count=3)
        if candidates:
            condition["region"] = " ".join(candidates)
            print(
                f"[search] 광역 지역 '{region}' + 라이프스타일 → "
                f"대표 구 {candidates} 로 검색"
            )
        else:
            # 라이프스타일 힌트도 없으면 인기 구 3개로 폴백
            fallback_gus = ["강남구", "마포구", "송파구"]
            condition["region"] = " ".join(fallback_gus)
            print(f"[search] 광역 지역 '{region}' → 인기 구 {fallback_gus} 로 폴백 검색")

    # verify 재시도 횟수만큼 인접 구 확장 (0회: 그대로, 1회: +1, 2회: +2)
    neighbor_count = verify_retry

    # 인접 구를 검색하면 verify의 region 체크가 막으므로 condition.region을 확장
    if neighbor_count > 0:
        base_gu = get_base_gu(condition.get("region", ""))
        if base_gu:
            extras = NEIGHBOR_GU.get(base_gu, [])[:neighbor_count]
            if extras:
                condition["region"] = " ".join([base_gu, *extras])
                print(f"[search] verify 재시도 → region 확장: '{condition['region']}'")

    print(f"[search] 실거래 API 조회 (relaxed={relaxed}, neighbors={neighbor_count})")

    # 매 검색마다 이전 stats 잔재 초기화 (이전 turn의 카운터가 새 결과에 새지 않도록)
    base_state = {
        **state,
        "search_results":   [],
        "filtered_results": [],
        "filter_stats":     {},
    }

    try:
        search_results = search_real_properties_expanded(condition, neighbor_count)
    except EnvironmentError as e:
        return {**base_state, "error_message": str(e)}
    except Exception as e:
        return {**base_state, "error_message": f"API 오류: {e}"}

    if not search_results:
        # MOLIT가 빈 결과 → 지역코드 미지원 또는 해당 기간 거래 없음
        return {
            **base_state,
            "error_message": (
                f"'{condition.get('region', '')}' 지역에서 실거래 데이터를 가져오지 못했어요. "
                "지역명을 더 구체적으로 적거나(예: '성동구', '강남구'), "
                "구 단위로 입력해 주세요."
            ),
        }

    print(f"[search] {len(search_results)}건 수집")

    # relaxed 모드: 소프트 조건 완화
    filter_cond = dict(condition)
    if relaxed:
        for key in ("min_households", "building_structure", "direction", "preferred_floor"):
            filter_cond.pop(key, None)
        print("[search] 소프트 조건 완화 적용")

    filter_stats: dict = {}
    filtered_results = filter_and_score_raw(search_results, filter_cond, lifestyle, stats=filter_stats)
    print(f"[search] 필터 통과 {len(filtered_results)}건")

    rejected = filter_stats.get("rejected_by", {})
    if rejected and not filtered_results:
        top3 = sorted(rejected.items(), key=lambda x: -x[1])[:3]
        summary = ", ".join(f"{r} {n}건" for r, n in top3)
        print(f"[search] 주요 탈락 사유: {summary}")

    return {
        **state,
        "condition":        condition,  # 확장된 region이 verify에서도 통과되도록 반영
        "search_results":   search_results,
        "filtered_results": filtered_results,
        "filter_stats":     filter_stats,
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

    # 매물 없음 → 필터 통계로 구체적 원인 진단
    if not filtered:
        filter_stats = state.get("filter_stats") or {}
        rejected     = filter_stats.get("rejected_by") or {}
        data_gaps    = filter_stats.get("data_gaps") or {}
        total_rejected = sum(rejected.values())

        # MOLIT 자체가 빈 결과를 준 경우 (지역코드 미지원 등) — error_message가 있고 rejected가 없음
        err_msg = state.get("error_message")
        search_count = len(state.get("search_results") or [])
        if err_msg and search_count == 0 and not rejected:
            return {
                **state,
                "recommendations": (
                    "❌ 검색 자체가 안 됐어요.\n\n"
                    f"⚠️ {err_msg}\n\n"
                    "💡 시도해볼만한 조정:\n"
                    "• 지역을 **구 단위**로 명시해보세요 (예: '성동구', '강남구')\n"
                    "• 동·역 이름 조합('금호동 옥수역')은 매핑이 부정확할 수 있어요\n"
                    "• 최근 3개월 내 거래가 없는 구일 수도 있어요 — 인근 구를 추가해보세요"
                ),
            }

        msg_lines = ["❌ 조건에 정확히 맞는 매물을 찾지 못했어요."]

        if rejected:
            top = sorted(rejected.items(), key=lambda x: -x[1])
            top_reason, top_count = top[0]
            pct = (top_count * 100) // total_rejected if total_rejected else 0

            msg_lines.append("")
            msg_lines.append(f"🔍 **0건이 된 핵심 이유**: {top_reason} ({top_count}건, 약 {pct}%)")

            # 상위 3개 사유 막대그래프 형식
            msg_lines.append("")
            msg_lines.append("📊 단계별 탈락 분포 (전체 {} 건 중):".format(total_rejected))
            for reason, n in top[:5]:
                bar = "█" * max(1, (n * 30) // top_count)
                msg_lines.append(f"  {reason:24} {bar} {n}건")

        # 데이터 자체 한계 (사용자 잘못 아님)
        gap_msgs = []
        if data_gaps.get("subway_minutes_missing", 0) > 0:
            gap_msgs.append(
                "• 실거래가 API에 **지하철역 거리 정보가 없습니다**. "
                "'역까지 N분' 조건을 빼거나 다른 조건으로 시도해 주세요."
            )
        if data_gaps.get("total_floors_missing", 0) > 0 or condition.get("top_floor_only"):
            gap_msgs.append(
                "• 실거래가 API에 **총 층수 정보가 없습니다**. "
                "'탑층' 조건은 매칭이 불가하니 '고층' 조건으로 바꿔보세요."
            )
        if gap_msgs:
            msg_lines.append("")
            msg_lines.append("⚠️ 데이터 한계로 매칭이 어려운 조건:")
            msg_lines.extend(gap_msgs)

        # 완화 제안 — top reason 기반 우선 제안
        suggestions: list[str] = []
        if "가격" in str(rejected):
            top_price_reasons = [r for r in rejected if "가격" in r]
            if top_price_reasons:
                if any(condition.get(k) for k in ("max_price", "max_deposit", "max_monthly")):
                    suggestions.append("• 가격 상한을 10~20% 정도 높여보세요")
        if "면적" in str(rejected) and condition.get("min_area"):
            suggestions.append(f"• 최소 면적({condition['min_area']}m²) 기준을 낮춰보세요")
        if "세대수" in str(rejected) and condition.get("min_households"):
            suggestions.append(f"• 최소 세대수({condition['min_households']}세대) 기준을 낮춰보세요")
        if "역세권" in str(rejected) or "역까지" in str(rejected):
            suggestions.append("• '역까지 N분' 조건 자체를 빼는 것도 고려해보세요 (실거래 데이터엔 정보 없음)")
        if "방종류" in str(rejected) and condition.get("property_type"):
            suggestions.append(f"• '{condition['property_type']}' 외 다른 유형도 고려해보세요")
        if condition.get("region"):
            suggestions.append(f"• '{condition['region']}' 외 인근 지역도 추가해보세요")

        if suggestions:
            msg_lines.append("")
            msg_lines.append("💡 시도해볼만한 조정:")
            msg_lines.extend(suggestions)

        return {**state, "recommendations": "\n".join(msg_lines)}

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