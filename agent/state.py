from typing import TypedDict, Optional, List, Any


class UserCondition(TypedDict, total=False):
    region: Optional[str]
    deal_type: Optional[str]
    max_deposit: Optional[int]
    max_monthly: Optional[int]
    max_price: Optional[int]
    min_area: Optional[float]
    property_type: Optional[str]
    min_households: Optional[int]
    parking_required: Optional[bool]
    building_structure: Optional[str]
    max_subway_minutes: Optional[int]
    min_rooms: Optional[int]
    min_bathrooms: Optional[int]
    preferred_floor: Optional[str]
    direction: Optional[str]
    max_building_age: Optional[int]


class AgentState(TypedDict):
    user_input: str
    condition: UserCondition
    is_valid: bool
    error_message: Optional[str]
    # clarify 노드가 생성한 질문. 값이 있으면 API가 프론트에 반환하고 그래프를 종료함.
    clarify_question: Optional[str]
    search_results: List[dict]
    filtered_results: List[dict]
    recommendations: str
    retry_count: int
    verify_retry_count: int
    # verify 재시도 시 True로 설정 → LLM에 조건 완화 신호를 줌
    relaxed: bool
    messages: List[Any]