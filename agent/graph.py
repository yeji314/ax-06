from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver # 추가됨

from agent.nodes import (
    clarify_node,
    parse_condition_node,
    recommend_node,
    search_and_filter_node,
    validate_node,
    verify_node,
)
from agent.state import AgentState

def _route_validate(state: AgentState) -> str:
    if state.get("is_valid"):            return "search"
    if state.get("retry_count", 0) >= 2: return "search"   # 2회 초과 → 강제 진행
    return "clarify"

def _route_verify(state: AgentState) -> str:
    if state.get("filtered_results"):              return "recommend"
    if state.get("verify_retry_count", 0) >= 2:    return "recommend"  # 한계 → 빈 결과로 추천
    return "search"

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("parse",    parse_condition_node)
    g.add_node("validate", validate_node)
    g.add_node("clarify",  clarify_node)
    g.add_node("search",   search_and_filter_node)
    g.add_node("verify",   verify_node)
    g.add_node("recommend",recommend_node)

    g.set_entry_point("parse")
    g.add_edge("parse",    "validate")
    g.add_conditional_edges("validate", _route_validate, {"search": "search", "clarify": "clarify"})
    g.add_edge("clarify",  END)         
    g.add_edge("search",   "verify")
    g.add_conditional_edges("verify", _route_verify, {"search": "search", "recommend": "recommend"})
    g.add_edge("recommend", END)

    # 상태 관리를 위한 MemorySaver 적용
    memory = MemorySaver()
    return g.compile(checkpointer=memory)