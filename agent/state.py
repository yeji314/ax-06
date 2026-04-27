from typing import Any, List, Optional, TypedDict


class UserCondition(TypedDict, total=False):
    """매물 스펙 조건"""
    region:             Optional[str]    # 지역 (예: "마포구")
    deal_type:          Optional[str]    # "월세" | "전세" | "매매"
    max_deposit:        Optional[int]    # 최대 보증금 (만원)
    max_monthly:        Optional[int]    # 최대 월세 (만원)
    max_price:          Optional[int]    # 최대 매매가 (만원)
    min_area:           Optional[float]  # 최소 면적 (m²)
    property_type:      Optional[str]    # "원룸" | "투룸" | "쓰리룸" | "아파트" | "오피스텔"
    min_households:     Optional[int]    # 최소 세대수
    parking_required:   Optional[bool]   # 주차 필수
    building_structure: Optional[str]    # "계단식" | "복도식"
    max_subway_minutes: Optional[int]    # 역까지 최대 도보 (분)
    min_rooms:          Optional[int]    # 최소 방 개수
    min_bathrooms:      Optional[int]    # 최소 욕실 개수
    preferred_floor:    Optional[str]    # "저층" | "중층" | "고층"
    top_floor_only:     Optional[bool]   # 탑층(꼭대기 층) 강제 — floor == total_floors
    direction:          Optional[str]    # "남향" | "동향" | "서향" | "북향" 등
    max_building_age:   Optional[int]    # 최대 건물 연식 (년)


class UserLifestyle(TypedDict, total=False):
    """생활권·분위기 조건"""
    activities:   List[str]     # ["런닝", "자전거", "등산", "수영", "헬스"]
    atmosphere:   Optional[str] # "조용한" | "활발한" | "자연친화적" | "카페거리" | "번화가"
    amenities:    List[str]     # ["공원", "한강", "카페", "헬스장", "마트", "병원"]
    raw_keywords: Optional[str] # 원문 (예: "런닝하기 좋은 조용한 동네")


class AgentState(TypedDict):
    user_input:         str
    condition:          UserCondition
    lifestyle:          UserLifestyle
    is_valid:           bool
    error_message:      Optional[str]
    clarify_question:   Optional[str]   # 값이 있으면 API가 프론트에 반환 후 그래프 종료
    search_results:     List[dict]      # 실거래 API 수집 매물
    filtered_results:   List[dict]      # 필터링·검증 통과 매물
    filter_stats:       dict            # 필터 단계별 탈락 사유 집계
    recommendations:    str             # AI 추천 코멘트
    retry_count:        int             # clarify 재시도 횟수
    verify_retry_count: int             # verify 재시도 횟수
    relaxed:            bool            # True면 소프트 조건 완화
    messages:           List[Any]