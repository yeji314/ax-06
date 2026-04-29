import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")
warnings.filterwarnings("ignore", message=".*LibreSSL.*")
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")

from typing import Any, Literal, Optional, Union
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

from agent.graph import build_graph
from agent.state import AgentState

app = FastAPI(
    title="부동산 추천 Agent API",
    description="LangGraph + 국토부 실거래가 API 기반 부동산 추천",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 애플리케이션 수명 주기 동안 유지될 글로벌 그래프 인스턴스 (메모리 유지용)
agent_graph = build_graph()

class RecommendRequest(BaseModel):
    user_input: str
    thread_id: str  # 프론트엔드에서 세션 ID를 받음

class ClarifyResponse(BaseModel):
    status: Literal["needs_clarification"]
    question: str       
    original_input: str 

class RecommendResponse(BaseModel):
    status: Literal["ok"]
    condition: dict[str, Any]       
    lifestyle: dict[str, Any]       
    is_valid: bool
    error_message: Optional[str]
    search_count: int               
    filtered_count: int             
    filtered_results: list[dict[str, Any]]
    recommendations: str            

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/recommend", response_model=Union[ClarifyResponse, RecommendResponse])
def recommend(req: RecommendRequest):
    if not req.user_input.strip():
        raise HTTPException(status_code=400, detail="user_input이 비어 있습니다.")

    # 스레드 ID 설정
    config = {"configurable": {"thread_id": req.thread_id}}

    # 기존에 진행 중인 상태가 있다면 업데이트, 없다면 새로 시작
    # Graph에 들어갈 입력값 (이전 상태는 MemorySaver가 알아서 병합함)
    input_state = {"user_input": req.user_input}

    try:
        final_state = agent_graph.invoke(input_state, config=config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 실행 오류: {e}")

    if final_state.get("clarify_question"):
        # clarify 상태일 경우 그래프가 중단되므로 프론트로 질문 반환
        return ClarifyResponse(
            status="needs_clarification",
            question=final_state["clarify_question"],
            original_input=req.user_input,
        )

    # 추천이 완료된 경우 clarify 질문 초기화 후 결과 반환
    agent_graph.update_state(config, {"clarify_question": None})

    return RecommendResponse(
        status="ok",
        condition=final_state.get("condition", {}),
        lifestyle=final_state.get("lifestyle", {}),
        is_valid=final_state.get("is_valid", False),
        error_message=final_state.get("error_message"),
        search_count=len(final_state.get("search_results", [])),
        filtered_count=len(final_state.get("filtered_results", [])),
        filtered_results=final_state.get("filtered_results", []),
        recommendations=final_state.get("recommendations", ""),
    )