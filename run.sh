#!/bin/bash

# ── 색상 정의 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ── 종료 시 자식 프로세스 정리 ───────────────────────────────────────────────
cleanup() {
    echo -e "\n${YELLOW}[종료] 서버를 종료합니다...${NC}"
    kill $FASTAPI_PID $STREAMLIT_PID 2>/dev/null
    wait $FASTAPI_PID $STREAMLIT_PID 2>/dev/null
    echo -e "${GREEN}[완료] 모든 서버가 종료되었습니다.${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Python 실행기 결정 ────────────────────────────────────────────────────────
# python3 → python 순으로 사용 (PATH에 uvicorn/streamlit 명령어가 없어도 동작)
if command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

# 패키지 사전 점검
if ! "$PY" -c "import uvicorn, streamlit" >/dev/null 2>&1; then
    echo -e "${RED}[오류] uvicorn 또는 streamlit이 설치돼 있지 않습니다.${NC}"
    echo -e "${YELLOW}다음 명령으로 의존성을 먼저 설치하세요:${NC}"
    echo -e "  ${CYAN}$PY -m pip install -r requirements.txt${NC}"
    exit 1
fi

# ── FastAPI 실행 ──────────────────────────────────────────────────────────────
echo -e "${CYAN}[FastAPI] 서버 시작 중... (http://localhost:8000)${NC}"
"$PY" -m uvicorn api:app --reload --host 0.0.0.0 --port 8000 &
FASTAPI_PID=$!

# FastAPI가 뜰 때까지 잠깐 대기
sleep 2

# 헬스체크
if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${GREEN}[FastAPI] 정상 실행됨 ✅${NC}"
else
    echo -e "${YELLOW}[FastAPI] 아직 준비 중... (계속 진행)${NC}"
fi

# ── Streamlit 실행 ────────────────────────────────────────────────────────────
echo -e "${CYAN}[Streamlit] 서버 시작 중... (http://localhost:8501)${NC}"
"$PY" -m streamlit run streamlit_app.py --server.port 8501 --server.address 0.0.0.0 &
STREAMLIT_PID=$!

# ── 실행 정보 출력 ────────────────────────────────────────────────────────────
echo -e ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  서버가 실행 중입니다${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "  FastAPI  : ${CYAN}http://localhost:8000${NC}"
echo -e "  API Docs : ${CYAN}http://localhost:8000/docs${NC}"
echo -e "  Streamlit: ${CYAN}http://localhost:8501${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "  종료하려면 ${YELLOW}Ctrl+C${NC} 를 누르세요"
echo -e "${GREEN}========================================${NC}"
echo -e ""

# ── 두 프로세스가 살아있는 동안 대기 ─────────────────────────────────────────
wait $FASTAPI_PID $STREAMLIT_PID