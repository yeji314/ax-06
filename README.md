# 🏠 부동산 추천 Agent

LangGraph 기반 State Machine으로 자연어 검색 조건을 분석하고, **국토교통부 실거래가 API**의 실데이터를 조회해 매물을 추천하는 대화형 AI Agent입니다.

> **AX 6조 · Workflow 트랙**
> 진입점: `python main.py` (CLI) · `./run.sh` (FastAPI + Streamlit)

---

## 🎯 핵심 특징

- **국토부 실거래가 API 연동** — LLM이 매물을 상상하지 않고 실거래 데이터에서 가져옴
- **두 개의 자기복원 루프** — 조건 부족 시 재질문, 검증 실패 시 인접 구로 재검색
- **자연어 깊이 추론** — "한강 근처", "OO역 근처", "학군 좋은", "중국인 많지 않은", "회사가 시청역인데 1시간 이내" 등 모호한 표현 모두 자동 해석
- **단계별 탈락 진단** — 0건일 때 어느 조건이 막았는지 막대그래프로 시각화
- **멀티턴 대화** — `MemorySaver`로 이전 조건 누적, "20억으로 다시" 같은 후속 명령 가능
- **3가지 인터페이스** — CLI · FastAPI · Streamlit Web UI

---

## 🔄 Agent 구조 (Flow)

```
[START]
   │
   ▼
┌────────────────────┐
│  parse_condition   │  자연어 → JSON (Pydantic structured output)
│                    │  + 16개 정규식 보강 (호선·역·한강·통근 등)
└──────────┬─────────┘
           ▼
┌────────────────────┐
│      validate      │  지역·거래유형·방종류·가격 4가지 필수 체크
└──────────┬─────────┘
           │
   ┌───────┴────────────┐
조건 부족 (retry<2)    조건 충족
     ▼                   ▼
┌───────────┐    ┌─────────────────────────┐
│  clarify  │    │  search_and_filter      │
│  질문 생성 │    │  ① 광역→대표구·인접구·동 │
│  + 그래프  │    │  ② MOLIT 실거래가 API   │
│   종료     │    │  ③ 20+ 하드 필터        │
└─────┬─────┘    │  ④ 점수화 → 상위 5건    │
      │          └────────────┬────────────┘
      │ 다음 invoke            ▼
      │ (MemorySaver           ┌──────────┐
      │  자동 머지)            │  verify  │  가격·유형·지역 재검증
      │                        └────┬─────┘
      │                             │
      │                ┌────────────┴────────────┐
      │           통과 0건 (retry<2)         통과 ≥ 1
      │                ▼                         ▼
      │          search 재실행            ┌──────────────┐
      │       (인접 구 +1, relaxed=True)   │  recommend   │  + Tavily 동네 정보
      │                                    └────┬─────────┘
      │                                         ▼
      │                                       [END]
      │
      └──────────  사용자 답변 → parse 재진입
```

**두 개의 자기복원 루프**

1. **clarify 루프** — 질문 부족 시 사용자 답변 ↔ parse·validate 사이클 (최대 2회)
2. **verify 재검색 루프** — 결과 부족 시 인접 구 점진 확장 + 소프트 조건 완화 (최대 2회)

---

## ⚙️ 설치 방법

```bash
git clone <repo>
cd final_ax6
pip install -r requirements.txt
```

---

## 🔑 환경변수 설정

`.env.example`을 복사해 `.env` 파일을 만듭니다:

```bash
cp .env.example .env
```

| 키 | 필수 | 설명 |
|----|------|------|
| `OPENAI_API_KEY` | ✅ 필수 | 자연어 파싱·재질문·추천 멘트 생성 (`gpt-4o-mini`) |
| `MOLIT_API_KEY` | ✅ 권장 | 국토부 실거래가 API. 미설정 시 빈 결과 |
| `TAVILY_API_KEY` | 선택 | 추천 멘트 생성 시 동네 정보 웹 검색 |
| `LANGSMITH_API_KEY` | 선택 | LLM 호출 추적 (관측성) |

**MOLIT API 키 발급** ([data.go.kr](https://www.data.go.kr) 무료, 5분)

활용 신청 4종 모두 신청 (같은 serviceKey 사용):
- 국토교통부_아파트매매 실거래자료
- 국토교통부_아파트 전월세 자료
- 국토교통부_연립다세대 매매 실거래자료
- 국토교통부_연립다세대 전월세 자료

---

## ▶️ 실행 방법

### 옵션 1 — CLI (검수 표준)
```bash
python main.py
```
멀티턴 대화. `q` 종료, `n` 세션 초기화.

### 옵션 2 — FastAPI + Streamlit 동시 실행
```bash
./run.sh
```
- FastAPI Swagger: http://localhost:8000/docs
- **Streamlit UI: http://localhost:8501** ← 일반 사용자용

### 옵션 3 — 따로 실행
```bash
uvicorn api:app --reload --port 8000
streamlit run streamlit_app.py --server.port 8501
```

---

## 💬 예시 입출력

### 시나리오 1 — 정상 검색
```
🏡 어떤 매물을 찾으시나요? 마포구 월세 보증금 3000 월 80 이하 투룸

[parse] 조건={'region':'마포구','deal_type':'월세','max_deposit':3000,
              'max_monthly':80,'property_type':'투룸'}
[search] 실거래 API 조회
[MOLIT] 마포구 월세 3개월: 빌라 326건 = 총 326건
[search] 필터 통과 5건
[verify] 5건 → 4건 통과

🏆 1순위: 마포구 도화동 OO빌라 (보증금 3,000 / 월 80, 5호선 공덕역 도보 5분)
```

### 시나리오 2 — 멀티턴 후속 검색
```
🏡 → 마포구 월세 보증금 3000 월 80 이하 투룸    (Turn 1)
🏆 추천 매물 4건

👉 → 보증금 5000까지 올려줘                    (Turn 2, 같은 thread)
[parse] 조건={...prior..., 'max_deposit':5000}  ← 이전 조건 머지
🏆 추천 매물 12건

👉 → n                                        (세션 초기화)
🆕 새 검색을 시작합니다.
```

### 시나리오 3 — 0건 진단
```
🏡 → 옥수역 근처 18억 이하 매매 아파트

[search] 역 인근 모드 → 도보권 동: ['옥수동','금호동','응봉동']
[MOLIT] 성동구 매매 3개월: 아파트 297건
[search] 역 인근 동 필터: 297건 → 111건
[search] 필터 통과 5건
[verify] 5건 → 0건 통과

❌ 조건에 정확히 맞는 매물을 찾지 못했어요.

🔍 핵심 이유: 매매가 초과 (96건 / 96%)

📊 단계별 탈락 분포 (전체 100건)
────────────────────────────────────────────────
  매매가 초과       ██████████████████  96%   96건
  방종류 불일치     █░░░░░░░░░░░░░░░░░   4%    4건
────────────────────────────────────────────────

💡 시도해볼만한 조정:
• 가격 상한을 10~20% 정도 높여보세요
• '옥수역' 외 인근 지역도 추가해보세요
```

### 시나리오 4 — 자연어 추론
```
"학군 좋은 매매 15억 이하 아파트"
  → region 자동 추론: 강남구·서초구·양천구 (LIFESTYLE_KEYWORD_TO_GU)

"한강 근처 매매 15억 아파트"
  → region: 용산·마포·영등포 + 한강 인접 동만 strict 필터링 (35개 동)

"중국인 많지 않은 강남 매매"
  → 대림동·자양동·이태원동 등 외국인 밀집 동 제외

"회사가 시청역인데 대중교통 1시간 이내"
  → commute_from='시청역', max_commute_minutes=60
  → 5개 거점 × 25개 구 정적 매트릭스로 추정 후 한도 초과 동 제외

"2호선 근처 매매"
  → 2호선 대표 구(마포·성동·강남) 동시 검색

"D2호선 근처"  ← IME 오타
  → '2호선'으로 자동 정제
```

---

## 🧱 State 구조

### `UserCondition` — 21개 매물·환경 조건

| 분류 | 필드 |
|------|------|
| **필수 4** | region · deal_type(월세/전세/매매) · property_type(아파트/원룸 등) · price |
| 매물 스펙 | min_area · min_households · parking_required · building_structure · min_rooms · min_bathrooms · preferred_floor · top_floor_only · direction · max_building_age |
| 환경·생활 | max_subway_minutes · exclude_high_foreign_density · hangang_view_only · commute_from · max_commute_minutes |

### `UserLifestyle` — 4개 생활권 키워드

```python
activities:   ["런닝","자전거","등산","수영","헬스"] 중
atmosphere:   "조용한"|"활발한"|"자연친화적"|"카페거리"|"번화가"
amenities:    ["공원","한강","카페","헬스장","마트","병원","학교","편의점"] 중
raw_keywords: 사용자 원문
```

---

## 🔧 사용 Tool 설명

### `tools/molit_api.py` — 국토교통부 실거래가 API ⭐ 메인

- 4개 엔드포인트(아파트/빌라 × 매매/전월세) 지원
- `property_type`에 따라 호출 엔드포인트 자동 선택 (불필요한 호출 차단)
- 최근 3개월 페이지네이션 수집 (페이지당 1000건 × 5페이지)
- **다중 룩업 테이블 내장**:
  - `LAWD_CD_MAP` — 서울 25구 + 경기 주요 시 법정동 코드 (31개)
  - `LANDMARK_TO_GU` — 역명·동명·랜드마크 → 구 매핑 (60+ 항목)
  - `NEIGHBOR_GU` — 25개 구 인접 그래프 (verify 재시도용)
  - `STATION_TO_NEAR_DONGS` — 50+ 역 → 도보권 법정동
  - `SUBWAY_LINE_TO_GU` — 12개 노선 → 대표 구
  - `LIFESTYLE_KEYWORD_TO_GU` — 16개 라이프스타일 키워드 → 구
  - `HANGANG_RIVERSIDE_DONGS` — 한강 인접 35개 법정동
  - `HIGH_FOREIGN_DENSITY_DONGS` — 외국인 밀집 7개 동
  - `COMMUTE_TIME_FROM_HUB` — 5개 거점 × 25구 = 125개 통근 시간 추정치
  - `OFFICETEL_NAME_HINTS` / `VILLA_NAME_HINTS` / `APT_BRAND_WHITELIST` — 매물 이름 재분류용

### `tools/filter_tool.py` — 필터링 + 점수화

**20+ 하드 필터** (각각 탈락 사유 카운팅):
거래유형 / 외국인 밀집 동 제외 / 한강변 동 제한 / 통근 시간 / 방종류(다중값) / 가격 / 면적 / 세대수 / 주차 / 구조 / 역세권 / 방·욕실 수 / 방향 / 층대 / 탑층 / 연식

**점수 공식**:
```
가격 통과 +30 / 면적 충족 +20 / 역세권7분 +15 / 주차 +5
+ 옵션 매칭 5점씩 / features 3점씩 / lifestyle_score×0.3
```

**데이터 갭 추적** (data_gaps): subway_minutes_missing · total_floors_missing · commute_unknown

### `tools/web_search_tool.py` — Tavily 동네 정보

- **추천 멘트 생성 컨텍스트로만 사용** (매물 데이터로는 사용 X)
- `include_domains=[land.naver.com, hogangnono.com, rtms.molit.go.kr ...]` 신뢰 도메인 우선
- 키 없으면 빈 리스트 반환 → 흐름 안 끊김

### `tools/llm_search_tool.py` — LLM 매물 생성 (대체용)

- 현재 메인 흐름에서 사용 안 함. 폴백·실험 용도

---

## 🛡️ 자연어 안전망 — 16개 정규식 보강

[agent/nodes.py::parse_condition_node](agent/nodes.py)는 LLM 결과를 16개 단계로 검증·보강합니다.

| # | 단계 | 효과 |
|---|------|------|
| 1 | `_sanitize_input` | "D2호선" → "2호선" (한글 IME 오타 정제) |
| 2 | property_type 화이트리스트 | "중아파트" 같은 LLM 환각 거부 |
| 3 | property_type 명시 체크 | 사용자 입력에 없으면 추론 거부 |
| 4 | deal_type 명시 체크 | "15억"으로부터 매매 추론 차단 |
| 5 | property_type 다중값 | "오피스텔, 빌라" 정규화 |
| 6 | `_filter_lifestyle` | 입력 없는 lifestyle 키워드 거부 |
| 7 | 억 단위 자동 교정 | "20억" → 200,000만원 (LLM 5× 오차 자동 보정) |
| 8 | 지하철 노선 추출 | `\d+호선/분당선/GTX-A` 등 region에 보강 |
| 9 | 라이프스타일 키워드 감지 | 입력에서 직접 amenities 채움 |
| 10 | region 자동 백필 | region 비었으면 lifestyle로 자동 추론 |
| 11 | 외국인 부정 패턴 | "중국인 많지 않은" → exclude flag |
| 12 | 한강 근접 패턴 | "한강 근처/뷰" → hangang_view_only |
| 13 | 통근 거점 추출 | "회사가 시청역" → commute_from |
| 14 | 통근 시간 추출 | "1시간 이내" → max_commute_minutes |
| 15 | 탑층 패턴 | "탑층/꼭대기/최상층" → top_floor_only |
| 16 | verify 카운터 리셋 | 새 검색마다 retry 상태 초기화 |

---

## 📊 0건 진단 UI

검증 실패 시 어떤 조건이 얼마나 막았는지 한눈에:

```
❌ 조건에 정확히 맞는 매물을 찾지 못했어요.

🔍 핵심 이유: 매매가 초과 (1,074건 / 56%)

📊 단계별 탈락 분포 (전체 1,898건)
────────────────────────────────────────────────────────
  매매가 초과       ██████████████████  56%  1,074건
  면적 미달         ███████████░░░░░░░  37%    706건
  방종류 불일치     █░░░░░░░░░░░░░░░░░   5%     95건
  탑층 확인 불가    █░░░░░░░░░░░░░░░░░   1%     23건
────────────────────────────────────────────────────────

⚠️ 데이터 한계로 매칭이 어려운 조건:
• 실거래가 API에 총 층수 정보가 없어 '탑층' 매칭 불가

💡 시도해볼만한 조정:
• 가격 상한을 10~20% 정도 높여보세요
• 인근 다른 구도 함께 찾아보세요
```

---

## 📁 프로젝트 구조

```
final_ax6/
├── main.py                  # CLI 진입점 (python main.py)
├── api.py                   # FastAPI 서버
├── streamlit_app.py         # Streamlit UI (streamlit 패키지명 충돌 회피용 이름)
├── run.sh                   # FastAPI + Streamlit 동시 실행
├── test_molit.py            # MOLIT API 단독 테스트
├── requirements.txt
├── README.md
├── .env.example
├── agent/
│   ├── __init__.py
│   ├── state.py             # AgentState · UserCondition (21개 필드) · UserLifestyle
│   ├── graph.py             # LangGraph 빌드 + MemorySaver checkpointer
│   └── nodes.py             # 6개 노드 + 16개 정규식 보강
├── tools/
│   ├── __init__.py
│   ├── molit_api.py         # 국토부 실거래가 API + 9개 룩업 테이블
│   ├── filter_tool.py       # 20+ 하드 필터 + 점수화 + 진단 통계
│   ├── web_search_tool.py   # Tavily 동네 정보
│   └── llm_search_tool.py   # LLM 매물 생성 (대체)
├── data/
└── docs/
    └── PRESENTATION.md      # 발표 자료
```

---

## 🛠️ 기술 스택

**LangGraph** (StateGraph + MemorySaver) · **LangChain** · **Pydantic** structured output ·
**OpenAI GPT-4o-mini** · **국토교통부 실거래가 API** · **Tavily** 웹 검색 ·
**LangSmith** 추적 · **FastAPI** · **Streamlit** · **Rich CLI**

---

## ⚠️ 한계와 향후 개발

### 데이터 한계 (코드에 명시)
- **MOLIT 실거래가에 지하철 거리 정보 없음** → "역까지 N분" 조건은 데이터 부재로 매칭 불가
- **MOLIT 실거래가에 총층수 정보 없음** → "탑층" 조건은 데이터 부재로 매칭 불가
- **실거래 이력만 제공** (현재 매물 호가 아님) — 시세 동향 참고용
- **빌라 분류가 거침** (다세대주택·연립주택 구분 부정확)

### 휴리스틱
- **통근 시간 매트릭스**: 5개 거점 × 25개 구 정적 추정치 (±10분 오차)
- **빌라 브랜드 정규식**: `OO빌` suffix 패턴 + 화이트리스트 — 새 브랜드 등장 시 누락 가능
- **외국인 밀집 동**: 7개 동 하드코딩 — 통계청 KOSIS API 연동 시 정확도 ↑

### 향후 개발 방향
- **공공데이터포털 학교알리미 API** 연동 → 학군 정량 점수
- **카카오 로컬 + ODsay 대중교통 API** → 실시간 통근 계산
- **KOSIS 외국인 통계 API** → 동 단위 인구통계 정밀화
- **개인화 추천**: 과거 대화 이력 기반 선호도 학습 (RAG)
- **목적 기반 분기**: 실거주 vs 투자 분리

---

## 📜 참고 자료

- [국토교통부 실거래가 공개시스템](https://rt.molit.go.kr)
- [공공데이터포털 RTMS API](https://www.data.go.kr/data/15058747/openapi.do)
- [LangGraph 공식 문서](https://langchain-ai.github.io/langgraph/)
