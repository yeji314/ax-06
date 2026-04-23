from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import (
    parse_condition_node,
    validate_node,
    clarify_node,
    search_and_filter_node,
    verify_node,
    recommend_node,
)


def route_after_validate(state: AgentState) -> str:
    if state["is_valid"]:
        return "search"
    elif state.get("retry_count", 0) >= 2:
        return "search"
    else:
        return "clarify"


def route_after_verify(state: AgentState) -> str:
    """검증 통과 매물이 있으면 추천, 없으면 최대 2회까지 재검색."""
    if state.get("filtered_results"):
        return "recommend"
    if state.get("verify_retry_count", 0) >= 2:
        return "recommend"
    return "search"


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("parse", parse_condition_node)
    graph.add_node("validate", validate_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("search", search_and_filter_node)
    graph.add_node("verify", verify_node)
    graph.add_node("recommend", recommend_node)

    graph.set_entry_point("parse")
    graph.add_edge("parse", "validate")
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {"search": "search", "clarify": "clarify"},
    )
    graph.add_edge("clarify", "parse")
    graph.add_edge("search", "verify")
    graph.add_conditional_edges(
        "verify",
        route_after_verify,
        {"search": "search", "recommend": "recommend"},
    )
    graph.add_edge("recommend", END)

    return graph.compile()
