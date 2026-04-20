from typing import TypedDict, Optional, List, Any


class UserCondition(TypedDict, total=False):
    region: Optional[str]
    deal_type: Optional[str]
    max_deposit: Optional[int]
    max_monthly: Optional[int]
    max_price: Optional[int]
    min_area: Optional[float]
    property_type: Optional[str]


class AgentState(TypedDict):
    user_input: str
    condition: UserCondition
    is_valid: bool
    error_message: Optional[str]
    search_results: List[dict]
    filtered_results: List[dict]
    recommendations: str
    retry_count: int
    messages: List[Any]
