import json
import re
import unicodedata
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
            "지하철 노선 이름(예: 2호선, 7호선, 분당선, 신분당선). "
            "단, '한강'·'카페거리'·'학군' 같은 분위기/생활권 키워드는 region이 아니라 "
            "lifestyle.amenities 또는 lifestyle.atmosphere에 넣으세요."
        ),
        default=None,
    )
    deal_type: Optional[str] = Field(description="'월세' | '전세' | '매매'", default=None)
    max_deposit: Optional[int] = Field(description="보증금 (만원 단위 정수)", default=None)
    max_monthly: Optional[int] = Field(description="월세 (만원 단위 정수)", default=None)
    max_price: Optional[int] = Field(description="매매가 (만원 단위 정수)", default=None)
    min_area: Optional[float] = Field(description="최소 면적 (m²)", default=None)
    property_type: Optional[str] = Field(
        description=(
            "방 종류. 허용값: '원룸', '투룸', '쓰리룸', '아파트', '오피스텔', '빌라'. "
            "사용자가 여러 개를 원하면 쉼표로 구분 (예: '오피스텔, 빌라', '아파트,오피스텔')"
        ),
        default=None,
    )
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
    commute_from: Optional[str] = Field(
        description=(
            "출퇴근 거점(회사·학교 등)이 있는 역명 또는 구 이름. "
            "예: '회사가 시청역' → '시청역', '강남에서 일해' → '강남역'"
        ),
        default=None,
    )
    max_commute_minutes: Optional[int] = Field(
        description=(
            "통근 허용 최대 분. '1시간 이내'→60, '30분 거리'→30, "
            "'1시간 30분'→90 형태로 정수 변환"
        ),
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


_VALID_PROPERTY_TYPES = {"원룸", "투룸", "쓰리룸", "아파트", "오피스텔", "빌라"}


def _parse_property_types(value: str) -> list[str]:
    """property_type 문자열을 유효한 타입 리스트로 분해 (다중 선택 지원)."""
    if not value:
        return []
    parts = re.split(r"[,/\s]+", value.strip())
    return [p for p in parts if p in _VALID_PROPERTY_TYPES]
_VALID_DEAL_TYPES     = {"월세", "전세", "매매"}
_VALID_ACTIVITIES     = {"런닝", "자전거", "등산", "수영", "헬스"}
_VALID_AMENITIES      = {"공원", "한강", "카페", "헬스장", "마트", "병원", "학교", "편의점"}
_VALID_ATMOSPHERES    = {"조용한", "활발한", "자연친화적", "카페거리", "번화가"}


def _filter_lifestyle(raw_ls: dict, user_input: str) -> dict:
    """LLM이 추론·환각한 라이프스타일 항목을 거부.
    값이 enum에 있고 사용자가 입력에 명시한 경우에만 통과시킨다.
    """
    if not raw_ls:
        return {}

    activities = [
        a for a in (raw_ls.get("activities") or [])
        if a in _VALID_ACTIVITIES and a in user_input
    ]
    amenities = [
        a for a in (raw_ls.get("amenities") or [])
        if a in _VALID_AMENITIES and a in user_input
    ]
    atmosphere = raw_ls.get("atmosphere")
    if atmosphere and (atmosphere not in _VALID_ATMOSPHERES or atmosphere not in user_input):
        atmosphere = None

    raw_kw = raw_ls.get("raw_keywords")
    # raw_keywords는 사용자 텍스트의 부분 문자열이어야만 의미가 있다.
    if raw_kw and raw_kw not in user_input:
        raw_kw = None
    # 모든 다른 필드가 비었으면 raw_keywords도 무의미 → 제거
    if not (activities or amenities or atmosphere):
        raw_kw = None

    cleaned = {}
    if activities: cleaned["activities"] = activities
    if amenities:  cleaned["amenities"]  = amenities
    if atmosphere: cleaned["atmosphere"] = atmosphere
    if raw_kw:     cleaned["raw_keywords"] = raw_kw
    return cleaned


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

    # property_type: 다중 값 지원 ("오피스텔, 빌라" → "오피스텔,빌라")
    pt = parsed.get("property_type")
    if pt:
        valid_pts = _parse_property_types(pt)
        # 입력에 명시되지 않은 값은 제거 (LLM 추론 차단)
        valid_pts = [p for p in valid_pts if p in user_input]
        if not valid_pts:
            print(f"[parse 경고] property_type='{pt}' 유효값 없음 또는 입력 미명시 → 무시")
            parsed["property_type"] = None
        else:
            normalized = ",".join(valid_pts)
            if normalized != pt:
                print(f"[parse] property_type 정규화: '{pt}' → '{normalized}'")
            parsed["property_type"] = normalized

    dt = parsed.get("deal_type")
    if dt and dt not in _VALID_DEAL_TYPES:
        print(f"[parse 경고] 잘못된 deal_type='{dt}' → 무시")
        parsed["deal_type"] = None

    # 사용자가 명시적으로 말한 경우에만 인정 (LLM이 가격·문맥에서 추론하는 것 차단)
    if parsed.get("deal_type") and not any(dt in user_input for dt in _VALID_DEAL_TYPES):
        print(f"[parse] deal_type='{parsed['deal_type']}' 텍스트 미명시 → 추론 거부")
        parsed["deal_type"] = None

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
            "commute_from":       parsed.get("commute_from"),
            "max_commute_minutes": parsed.get("max_commute_minutes"),
        }.items() if v is not None
    }

    # 탑층/꼭대기 층 키워드는 LLM이 종종 놓침 → 정규식으로 보강
    if re.search(r"탑\s*층|꼭대기|맨\s*위층|최상층", user_input):
        new_fields["top_floor_only"] = True

    # 멀티턴: 이전 condition과 병합 (이전 정보 유지 + 새 정보로 덮어쓰기)
    prior_condition = dict(state.get("condition") or {})
    condition: UserCondition = {**prior_condition, **new_fields}

    # lifestyle: LLM 추론·환각 거르기 + 멀티턴 머지
    prior_ls = dict(state.get("lifestyle") or {})
    cleaned_ls = _filter_lifestyle(parsed.get("lifestyle") or {}, user_input)
    new_acts  = cleaned_ls.get("activities", [])
    new_amens = cleaned_ls.get("amenities", [])
    lifestyle: UserLifestyle = {
        "activities":   list(dict.fromkeys([*(prior_ls.get("activities") or []), *new_acts])),
        "amenities":    list(dict.fromkeys([*(prior_ls.get("amenities") or []), *new_amens])),
        "atmosphere":   cleaned_ls.get("atmosphere")   or prior_ls.get("atmosphere"),
        "raw_keywords": cleaned_ls.get("raw_keywords") or prior_ls.get("raw_keywords"),
    }

    # 지하철 노선이 입력에 있으면 region에 보강 (LLM이 놓치는 경우 안전망)
    subway_lines = _extract_subway_lines(user_input)
    if subway_lines:
        existing_region = condition.get("region", "") or ""
        missing_lines = [ln for ln in subway_lines if ln not in existing_region]
        if missing_lines:
            condition["region"] = (
                " ".join([existing_region, *missing_lines]).strip()
                if existing_region else " ".join(missing_lines)
            )
            print(f"[parse 보강] 노선 인식 → region='{condition['region']}'")

    # 라이프스타일 키워드를 입력에서 직접 감지해 lifestyle에 반영
    # (LLM이 raw_keywords를 안 채워도 동작하도록 안전망)
    from tools.molit_api import LIFESTYLE_KEYWORD_TO_GU
    detected_amens = []
    detected_raw   = []
    for kw in LIFESTYLE_KEYWORD_TO_GU:
        if kw in user_input and kw not in (lifestyle.get("amenities") or []):
            detected_amens.append(kw)
            detected_raw.append(kw)
    if detected_amens:
        lifestyle = {
            **lifestyle,
            "amenities": [*(lifestyle.get("amenities") or []), *detected_amens],
            "raw_keywords": lifestyle.get("raw_keywords") or " ".join(detected_raw),
        }
        print(f"[parse 보강] 라이프스타일 키워드 → {detected_amens}")

    # region이 비어 있는데 라이프스타일 힌트가 있으면 → 대표 구로 자동 채움
    # (예: '학군 좋은 매매 15억' → region='강남구 서초구 양천구')
    if not condition.get("region") and (
        lifestyle.get("amenities") or lifestyle.get("raw_keywords")
    ):
        from tools.molit_api import infer_gus_from_lifestyle as _infer_gus
        ls_gus = _infer_gus(lifestyle, max_count=3)
        if ls_gus:
            condition["region"] = " ".join(ls_gus)
            print(f"[parse 보강] 라이프스타일 → region 자동 추론 = {ls_gus}")

    # 부정 인구통계 패턴: '중국인/외국인 많지 않은' 등 → 외국인 밀집 동 제외 플래그
    if re.search(r"(중국인|외국인).{0,6}(많지\s*않|적은|없는|싫)", user_input):
        condition["exclude_high_foreign_density"] = True
        print("[parse 보강] '중국인/외국인 많지 않은' 감지 → exclude_high_foreign_density=True")

    # 한강 근접 패턴: '한강 근처/변/뷰/인접/주변' → 한강변 동만 필터링
    if re.search(r"한강\s*(근처|변|뷰|view|인접|옆|주변|조망)", user_input, re.IGNORECASE):
        condition["hangang_view_only"] = True
        print("[parse 보강] '한강 근처/뷰' 감지 → hangang_view_only=True (강변 동만 검색)")

    # 통근 거점 패턴: '회사가 시청역', '회사 강남', '출근 시청'
    if not condition.get("commute_from"):
        m = re.search(
            r"(?:회사|직장|출근|통근|학교)[은이가는\s]*([가-힣A-Za-z]+(?:역|구|동))",
            user_input,
        )
        if m:
            condition["commute_from"] = m.group(1)
            print(f"[parse 보강] 통근 거점 → commute_from='{m.group(1)}'")

    # 통근 시간 패턴: 'N시간 M분 이내', 'N시간 이내', 'N분 거리/이내' (도보가 아닌 경우)
    if not condition.get("max_commute_minutes"):
        if condition.get("commute_from") or re.search(r"(통근|출퇴근|회사까지|대중교통)", user_input):
            mh = re.search(r"(\d+)\s*시간(?:\s*(\d+)\s*분)?", user_input)
            mm = re.search(r"(?<!도보\s)(\d+)\s*분\s*(?:이내|거리|이하)", user_input)
            total = None
            if mh:
                total = int(mh.group(1)) * 60 + int(mh.group(2) or 0)
            elif mm:
                total = int(mm.group(1))
            if total:
                condition["max_commute_minutes"] = total
                print(f"[parse 보강] 통근 시간 → max_commute_minutes={total}분")

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
        LIFESTYLE_KEYWORD_TO_GU, NEIGHBOR_GU,
        get_base_gu, get_dongs_near_station, get_lawd_cd,
        infer_gus_from_lifestyle, infer_gus_from_subway_line,
        is_broad_region, search_real_properties_expanded,
    )

    condition = dict(state.get("condition", {}) or {})
    lifestyle = state.get("lifestyle", {}) or {}
    relaxed   = state.get("relaxed", False)
    verify_retry = state.get("verify_retry_count", 0)
    user_input = state.get("user_input", "") or ""

    # 0) region 또는 입력에 'OO역'이 있으면 도보권 동을 추출 → 사후 필터링용
    region = condition.get("region", "") or ""
    near_dongs = get_dongs_near_station(region) or get_dongs_near_station(user_input)
    if near_dongs:
        print(f"[search] 역 인근 모드 → 도보권 동: {near_dongs}")
        # verify의 region 체크가 'OO역' 토큰을 매물 district에서 못 찾고 모두 탈락시키는
        # 문제 차단 — condition.region에 도보권 동 이름을 추가해 매칭 가능하게 함
        existing_tokens = set(region.split())
        added = [d for d in near_dongs if d not in existing_tokens]
        if added:
            condition["region"] = " ".join([region.strip(), *added]).strip()
            print(f"[search] verify용 region 확장: '{condition['region']}'")

    # 1) region 자체에서 지하철 노선 감지 (예: "2호선 근처")
    line_gus = infer_gus_from_subway_line(region) or infer_gus_from_subway_line(user_input)

    if line_gus and (is_broad_region(region) or get_base_gu(region) is None):
        condition["region"] = " ".join(line_gus)
        print(f"[search] 지하철 노선 인식 → 대표 구 {line_gus} 로 검색")
    elif (
        region and not near_dongs and not get_lawd_cd(region)
        and any(kw == region.strip() for kw in LIFESTYLE_KEYWORD_TO_GU)
    ):
        # region 자체가 라이프스타일 키워드인 경우 (예: '한강', '카페거리')
        # → 해당 키워드의 대표 구로 검색하고 lifestyle.amenities에도 추가
        ls_gus = LIFESTYLE_KEYWORD_TO_GU[region.strip()][:3]
        condition["region"] = " ".join(ls_gus)
        amens = list(lifestyle.get("amenities") or [])
        if region.strip() not in amens:
            amens.append(region.strip())
        lifestyle = {**lifestyle, "amenities": amens}
        print(f"[search] '{region}'은 분위기 키워드 → 대표 구 {ls_gus} 로 변환")
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
    # ※ 역 인근 모드면 사용자가 특정 역을 명시한 것 → 인접 구 확장은 의도와 다름 → 비활성화
    neighbor_count = 0 if near_dongs else verify_retry

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

    # 역 인근 모드: 도보권 동에 속한 매물만 남김
    if near_dongs:
        before = len(search_results)
        search_results = [
            p for p in search_results
            if any(dong in (p.get("district") or "") for dong in near_dongs)
        ]
        print(f"[search] 역 인근 동 필터: {before}건 → {len(search_results)}건")

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
        "lifestyle":        lifestyle,  # 라이프스타일 키워드 region 변환 시 amenities에 추가됨
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

    # 탈락 사유별 카운트 (filter_stats에 합산해 recommend가 보여줄 수 있도록)
    verify_rejected: dict[str, int] = {}
    verified: list[dict] = []
    for p in filtered:
        if not _check_price(p, condition):
            verify_rejected["verify-가격"] = verify_rejected.get("verify-가격", 0) + 1
            continue
        if not _check_type(p, condition):
            verify_rejected["verify-거래유형"] = verify_rejected.get("verify-거래유형", 0) + 1
            continue
        if not _check_region(p, condition):
            verify_rejected["verify-지역"] = verify_rejected.get("verify-지역", 0) + 1
            continue
        verified.append(p)

    if verify_rejected:
        # 기존 filter_stats에 합산
        merged = dict(state.get("filter_stats") or {})
        merged.setdefault("rejected_by", {})
        for k, v in verify_rejected.items():
            merged["rejected_by"][k] = merged["rejected_by"].get(k, 0) + v
        state = {**state, "filter_stats": merged}
        summary = ", ".join(f"{r} {n}건" for r, n in verify_rejected.items())
        print(f"[verify] 탈락 사유: {summary}")

    print(f"[verify] {len(filtered)}건 → {len(verified)}건 통과")

    new_state = {**state, "filtered_results": verified}
    if not verified:
        retry = state.get("verify_retry_count", 0) + 1
        new_state["verify_retry_count"] = retry
        new_state["relaxed"]            = True
        print(f"[verify] 재시도 {retry}회차 예약")
    return new_state


# ── 표시 유틸 ─────────────────────────────────────────────────────────────────

def _vis_width(s: str) -> int:
    """한글·이모지 등 와이드 문자를 2칸으로 계산하는 시각 폭."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _vis_pad(s: str, target_width: int) -> str:
    """시각 폭 기준으로 우측에 공백 padding."""
    pad = max(0, target_width - _vis_width(s))
    return s + " " * pad


# 진단 메시지에서 길고 산만한 탈락 사유를 짧고 일관된 라벨로 축약
_REASON_SHORT = {
    "가격(매매가) 초과":              "매매가 초과",
    "가격(보증금) 초과":              "보증금 초과",
    "가격(월세) 초과":                "월세 초과",
    "가격(전세가) 초과":              "전세가 초과",
    "탑층 확정 불가(총층수 데이터 없음)": "탑층 확인 불가",
    "역세권 정보 없음(데이터 한계)":   "역세권 정보 없음",
    "외국인 밀집 동 제외 요청":        "외국인 밀집 동",
    "거래유형 불일치":                "거래유형 불일치",
    "방종류 불일치":                 "방종류 불일치",
    "최소 면적 미달":                "면적 미달",
    "최소 세대수 미달":              "세대수 미달",
    "최소 방 수 미달":               "방 수 미달",
    "최소 욕실 수 미달":             "욕실 수 미달",
    "선호 방향 불일치":              "방향 불일치",
    "선호 층대 불일치":              "층대 불일치",
    "건물 구조 불일치":              "구조 불일치",
    "주차 불가":                    "주차 불가",
    "탑층 아님":                    "탑층 아님",
    "연식 초과":                    "연식 초과",
    "역까지 도보 시간 초과":          "역까지 도보 초과",
    "verify-가격":                  "[verify] 가격 재초과",
    "verify-거래유형":              "[verify] 거래유형 재불일치",
    "verify-지역":                  "[verify] 지역 재불일치",
}


def _short_reason(reason: str) -> str:
    if reason in _REASON_SHORT:
        return _REASON_SHORT[reason]
    if reason.startswith("통근 ") and "한도" in reason:
        return "통근 시간 초과"
    return reason


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
            msg_lines.append(
                f"🔍 핵심 이유: {_short_reason(top_reason)} "
                f"({top_count:,}건 / {pct}%)"
            )

            # 상위 5개 사유 막대그래프
            msg_lines.append("")
            msg_lines.append(f"📊 단계별 탈락 분포 (전체 {total_rejected:,}건)")
            msg_lines.append("─" * 56)

            label_w = 18  # 사유 시각 폭 (한글 9자 정도)
            bar_w   = 18  # 막대 최대 길이
            for reason, n in top[:5]:
                pct_n = (n * 100) // total_rejected if total_rejected else 0
                bar_len = max(1, (n * bar_w) // top_count) if top_count else 0
                bar = "█" * bar_len + "░" * (bar_w - bar_len)
                label = _vis_pad(_short_reason(reason), label_w)
                msg_lines.append(f"  {label} {bar} {pct_n:>3}%  {n:>5,}건")
            msg_lines.append("─" * 56)

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
        if "verify-지역" in rejected:
            suggestions.append(
                "⚠️ verify 단계 지역 매칭 실패 — 입력한 지역명과 실거래 매물의 행정동이 "
                "일치하지 않습니다. 보다 일반적인 지역명(예: 구·동)으로 시도해보세요."
            )
        if "verify-가격" in rejected:
            suggestions.append(
                "⚠️ verify 단계에서 상위 매물이 가격 상한을 초과했습니다 — filter는 통과해도 "
                "최종 검증에서 가격이 다시 체크됐어요. 상한을 더 올려보세요."
            )

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