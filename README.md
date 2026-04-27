# 🏠 부동산 추천 Agent

LangGraph 기반 State Machine으로 사용자의 자연어 조건을 분석하고, **국토교통부 실거래가 API**로 실데이터를 조회해 최적의 매물을 추천하는 AI Agent입니다.

> AX 6조 · Workflow 트랙
> 실행: `python main.py` (CLI) · `./run.sh` (FastAPI + Streamlit)

---

## 🔄 Agent 구조 (Flow)

```
[START]
   │
   ▼
┌─────────────────────┐
│  parse_condition    │  ← 자연어 → 매물 조건 + 생활권 (Pydantic structured output)
└─────────────────────┘
   │
   ▼
┌─────────────────────┐
│    validate         │  ← 지역·거래유형·가격 3가지 필수조건 체크
└─────────────────────┘
   │
   ├─── 부족 & retry<2 ──▶ ┌─────────────────┐
   │                        │    clarify      │  ← 부족 항목 LLM 재질문 (state에 저장 후 종료)
   │                        └─────────────────┘
   │                                │
   │                                └── 사용자 답변 → 다음 invoke 시 MemorySaver가 병합
   │
   ▼ (조건 충족 또는 retry≥2)
┌─────────────────────┐
│ search_and_filter   │  ← 국토부 실거래가 API 조회 → 점수 필터링
└─────────────────────┘
   │
   ▼
┌─────────────────────┐
│     verify          │  ← 가격·거래유형·지역 필수조건 재검증
└─────────────────────┘
   │
   ├── 통과 0건 & retry<2 ──▶ search 재실행 (relaxed=True 완화 모드)
   │
   ▼ (통과 ≥ 1 또는 retry≥2)
┌─────────────────────┐
│    recommend        │  ← 매물 + 동네 정보(Tavily)로 자연어 추천 멘트 생성
└─────────────────────┘
   │
   ▼
[END]
```

**핵심 설계**
- **단계별 평가/회귀**: clarify(질문 부족 시), verify(결과 부족 시) 두 개의 자기복원 루프
- **MemorySaver checkpointer**로 멀티턴 대화 지원 (`thread_id` 기반 세션)
- **Pydantic structured output**으로 JSON 파싱 안정성 확보 + 억 단위 계산 오류 자동 교정

---

## ⚙️ 설치 방법

```bash
cd final-ax6
pip install -r requirements.txt
```

---

## 🔑 환경변수 설정

`.env.example`을 복사해 `.env` 파일을 만들고 키를 입력합니다:

```bash
cp .env.example .env
```

**`.env` 파일 내용**
```
OPENAI_API_KEY=sk-...
MOLIT_API_KEY=...
TAVILY_API_KEY=tvly-...
LANGSMITH_API_KEY=ls__...
```

| 키 | 필수 | 설명 |
|----|------|------|
| `OPENAI_API_KEY` | ✅ 필수 | 조건 파싱·재질문·추천 멘트 생성 |
| `MOLIT_API_KEY` | ⚠️ 권장 | 국토부 실거래가 API. 미설정 시 실데이터 조회 빈 결과 |
| `TAVILY_API_KEY` | 선택 | 추천 멘트 생성 시 동네 정보 웹 검색 |
| `LANGSMITH_API_KEY` | 선택 | LLM 호출 추적 (관측성) |

**MOLIT 키 발급** — [data.go.kr](https://www.data.go.kr) → "국토교통부 아파트 매매 실거래자료" 등 4종 활용신청 (모두 같은 serviceKey 사용)
- 아파트 매매 실거래가 자료
- 아파트 전월세 자료
- 연립다세대 매매 실거래가 자료
- 연립다세대 전월세 자료

---

## ▶️ 실행 방법

### 옵션 A — CLI (검수자용 기본)
```bash
python main.py
```
인터랙티브 멀티턴 대화. 조건이 부족하면 자동으로 되묻습니다.

### 옵션 B — FastAPI + Streamlit 동시 실행
```bash
./run.sh
```
- FastAPI Swagger: http://localhost:8000/docs
- Streamlit UI:    http://localhost:8501

### 옵션 C — 따로 실행
```bash
uvicorn api:app --reload --port 8000
streamlit run streamlit.py --server.port 8501
```

> macOS에서 `python` 명령이 없으면 `python3 main.py` 사용.

---

## 💬 예시 입력/출력

### 시나리오 1 — 정상 조건
```
🏡 어떤 매물을 찾으시나요? 마포구 월세 보증금 3000 월 80 이하 투룸

[parse] 조건={'region': '마포구', 'deal_type': '월세', 'max_deposit': 3000, ...}
[search] 실거래 API 조회 (relaxed=False)
[MOLIT] 아파트 월세 202604 → 142건
[MOLIT] 빌라 월세 202604 → 87건
[verify] 5건 → 4건 통과

✨ 추천
🏆 1순위: 마포구 공덕동 OO아파트
- 5호선 공덕역 도보 5분 · 보증금 3,000 / 월 80
- 추천 이유: 예산 완벽 충족, 역세권, 남향
```

### 시나리오 2 — 불충분한 입력 → 재질문
```
🏡 어떤 매물을 찾으시나요? 왕십리 아파트 추천해줘

[clarify] 안녕하세요! 거래 방식과 예산을 알려주시면 바로...

🤔 추가 정보 필요
거래 유형(월세/전세/매매)과 예산을 알려주세요.
- 거래 유형: 예) 월세, 전세, 매매
- 예산: 예) 매매 20억 이하

👉 매매 15억 이하

(이후 정상 처리)
```

### 시나리오 3 — 결과 0건 → 조건 완화 안내
```
[verify] 6건 → 0건 통과 (재시도 1/2)
[search] 실거래 API 조회 (relaxed=True)
[verify] 6건 → 0건 통과 (재시도 2/2 → 추천 단계로)

🔎 조건을 조금만 조정해주시면 바로 다시 찾아드릴 수 있어요!
• 금액 상한을 조금 높여보세요
• 인근 다른 구도 함께 찾아보세요
```

---

## 🔧 사용 Tool 설명

### `tools/molit_api.py` — 국토교통부 실거래가 API
- 아파트/빌라 × 매매/전월세 4개 엔드포인트 호출
- 법정동 코드(LAWD_CD) 매핑 내장 (서울 25개 구 + 경기 주요 시)
- 최근 3개월 데이터 페이지네이션 수집
- 환경변수 `MOLIT_API_KEY` 필요

### `tools/web_search_tool.py` — Tavily 동네 정보 검색
- 네이버 부동산·호갱노노·실거래가·직방 등 신뢰 도메인 우선 검색
- **추천 코멘트 생성 시 동네 분위기·생활편의 컨텍스트로만 활용** (매물 데이터로는 사용하지 않음)
- 키 없으면 빈 리스트 → 흐름 중단 없음

### `tools/filter_tool.py` — 매물 필터링·점수화
- 하드 필터: 가격·면적·거래유형·매물유형·세대수·주차·구조·역세권·층·방향·연식
- 점수: 가격(+30) · 면적(+20) · 역세권7분이내(+15) · 주차(+5) · 옵션 매칭(+5씩) · features × 3 · 생활권(+최대 30)
- 상위 5건 반환

### `tools/llm_search_tool.py` — LLM 매물 생성 (보조)
- MOLIT 실거래 데이터 보강용으로 일부 사용 (relaxed 모드 시)

### `agent/nodes.py::verify_node` — 필수조건 재검증
- 추천 직전에 **가격·거래유형·지역** 3대 필수조건 매물별 재검증
- 통과 0건 시 `search`로 되돌아가 재실행 (최대 2회)

---

## 📁 프로젝트 구조

```
final-ax6/
├── main.py                 # CLI 진입점 (python main.py)
├── api.py                  # FastAPI 서버
├── streamlit.py            # Streamlit UI
├── run.sh                  # FastAPI + Streamlit 동시 실행 스크립트
├── requirements.txt
├── README.md
├── .env.example
├── test_molit.py           # MOLIT API 단독 테스트
├── agent/
│   ├── __init__.py
│   ├── state.py            # AgentState / UserCondition / UserLifestyle
│   ├── graph.py            # LangGraph 빌드 (MemorySaver 포함)
│   └── nodes.py            # 6개 노드 (parse/validate/clarify/search/verify/recommend)
├── tools/
│   ├── __init__.py
│   ├── molit_api.py        # 국토부 실거래가 API 클라이언트
│   ├── web_search_tool.py  # Tavily 웹 검색
│   ├── llm_search_tool.py  # LLM 매물 생성 (보조)
│   └── filter_tool.py      # 매물 필터·점수화
├── data/
└── docs/
    └── PRESENTATION.md     # 발표 자료
```

---

## 🧪 동작 검증

```bash
# MOLIT API 키 동작 확인
python test_molit.py

# CLI 데모
python main.py
```

---

## 🛠️ 기술 스택

LangGraph · LangChain · OpenAI GPT-4o-mini · Pydantic structured output ·
국토교통부 실거래가 API · Tavily 웹 검색 · LangSmith 추적 · FastAPI · Streamlit · Rich
