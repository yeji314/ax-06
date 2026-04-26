from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Optional, Literal

from dotenv import load_dotenv
load_dotenv()

# LangSmith 트레이싱은 .env의 환경변수로 자동 활성화됨.
# LANGCHAIN_TRACING_V2=true
# LANGCHAIN_API_KEY=ls__...
# LANGCHAIN_PROJECT=real-estate-agent

from agent.graph import build_graph
from agent.state import AgentState

app = FastAPI(
    title="부동산 추천 Agent API",
    description="LangGraph 기반 부동산 매물 추천 API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 요청/응답 스키마 ───────────────────────────────────────────────────────────

class RecommendRequest(BaseModel):
    user_input: str
    # clarify 단계에서 사용자가 답변을 보내면 이 필드로 전달
    clarify_answer: Optional[str] = None


class ClarifyResponse(BaseModel):
    """조건이 부족해 추가 질문이 필요한 경우 반환."""
    status: Literal["needs_clarification"]
    question: str
    original_input: str


class RecommendResponse(BaseModel):
    """검색 결과가 있는 경우 반환."""
    status: Literal["ok"]
    condition: dict[str, Any]
    is_valid: bool
    error_message: str | None
    search_count: int
    filtered_count: int
    filtered_results: list[dict[str, Any]]
    recommendations: str


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/recommend", response_model=ClarifyResponse | RecommendResponse)
def recommend(req: RecommendRequest):
    if not req.user_input.strip():
        raise HTTPException(status_code=400, detail="user_input이 비어 있습니다.")

    # clarify 답변이 있으면 원본 입력과 합쳐서 재파싱
    combined_input = req.user_input
    if req.clarify_answer and req.clarify_answer.strip():
        combined_input = f"{req.user_input} {req.clarify_answer.strip()}"
        print(f"[clarify 재진입] combined_input: {combined_input}")

    graph = build_graph()

    initial_state: AgentState = {
        "user_input":       combined_input,
        "condition":        {},
        "is_valid":         False,
        "error_message":    None,
        "clarify_question": None,
        "search_results":   [],
        "filtered_results": [],
        "recommendations":  "",
        "retry_count":      0,
        "verify_retry_count": 0,
        "relaxed":          False,
        "messages":         [],
    }

    try:
        final_state = graph.invoke(initial_state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 실행 오류: {str(e)}")

    # clarify 노드가 질문을 생성했으면 프론트에 돌려줌
    if final_state.get("clarify_question"):
        return ClarifyResponse(
            status="needs_clarification",
            question=final_state["clarify_question"],
            original_input=req.user_input,
        )

    return RecommendResponse(
        status="ok",
        condition=final_state.get("condition", {}),
        is_valid=final_state.get("is_valid", False),
        error_message=final_state.get("error_message"),
        search_count=len(final_state.get("search_results", [])),
        filtered_count=len(final_state.get("filtered_results", [])),
        filtered_results=final_state.get("filtered_results", []),
        recommendations=final_state.get("recommendations", ""),
    )