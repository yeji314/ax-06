from typing import TypedDict, Optional, List, Any


class UserCondition(TypedDict, total=False):
    # 위치
    region: Optional[str]
    # 거래 유형
    deal_type: Optional[str]
    # 금액대
    max_deposit: Optional[int]
    max_monthly: Optional[int]
    max_price: Optional[int]
    # 면적
    min_area: Optional[float]
    # 방 종류
    property_type: Optional[str]
    # 세대수
    min_households: Optional[int]
    # 주차여부
    parking_required: Optional[bool]
    # 계단식/복도식
    building_structure: Optional[str]
    # 근처 역까지 몇분
    max_subway_minutes: Optional[int]
    # 방 개수 / 욕실 개수
    min_rooms: Optional[int]
    min_bathrooms: Optional[int]
    # 층 / 방향
    preferred_floor: Optional[str]
    direction: Optional[str]
    # 연식 (최대 몇 년 된 건물까지 허용)
    max_building_age: Optional[int]


class AgentState(TypedDict):
    user_input: str
    condition: UserCondition
    is_valid: bool
    error_message: Optional[str]
    search_results: List[dict]
    filtered_results: List[dict]
    recommendations: str
    retry_count: int
    verify_retry_count: int
    messages: List[Any]
