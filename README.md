# 🏠 부동산 추천 Agent

LangGraph 기반 State Machine으로 사용자의 조건(지역, 예산, 면적 등)을 분석하여 최적의 부동산 매물을 추천하는 AI Agent입니다.

---

## 🔄 Agent 구조 (Flow)

```
[START]
   │
   ▼
┌─────────────────────┐
│  parse_condition    │  ← 자연어 입력을 조건(JSON)으로 파싱 (LLM)
└─────────────────────┘
   │
   ▼
┌─────────────────────┐
│    validate         │  ← 지역·거래유형·가격 3가지 필수조건 존재 여부 검증
└─────────────────────┘
   │
   ├─── is_valid=False & retry<2 ──▶ ┌─────────────────┐
   │                                  │    clarify      │  ← 부족한 조건 질문 (LLM)
   │                                  └─────────────────┘
   │                                         │
   │                                         └─── (루프) → parse_condition
   │
   ▼ (is_valid=True 또는 retry>=2)
┌─────────────────────┐
│ search_and_filter   │  ← LLM이 조건 기반 매물 생성 + 점수 필터링
└─────────────────────┘
   │
   ▼
┌─────────────────────┐
│     verify          │  ← 가격/유형/지역 필수조건 재검증
└─────────────────────┘
   │
   ├── 통과 매물 0건 & 재시도 < 2 ──▶ (search로 복귀, 재생성)
   │
   ▼ (통과 매물 ≥ 1 또는 재시도 ≥ 2)
┌─────────────────────┐
│    recommend        │  ← 자연어 추천 텍스트 생성 (LLM)
└─────────────────────┘
   │
   ▼
[END]
```

---

## ⚙️ 설치 방법

```bash
cd final-ax6
pip install -r requirements.txt
```

---

## 🔑 환경변수 설정

`.env.example`을 복사하여 `.env` 파일을 생성하고 OpenAI API 키를 입력합니다:

```bash
cp .env.example .env
```

`.env` 파일 내용:
```
OPENAI_API_KEY=sk-your-api-key-here
# 선택: Tavily 웹 검색 키 (https://app.tavily.com 무료 발급)
TAVILY_API_KEY=tvly-your-api-key-here
```

- `OPENAI_API_KEY`: 필수 (조건 파싱·매물 생성·추천 멘트에 사용)
- `TAVILY_API_KEY`: 선택. 설정하면 Agent가 **실시간 웹 검색**으로 최신 시세/단지 정보를 수집해 매물 생성 근거로 사용합니다. 미설정 시 LLM 단독 생성.

---

## ▶️ 실행 방법

```bash
python main.py
```

> macOS 환경에서 `python` 명령어를 찾을 수 없는 경우:
> ```bash
> python3 main.py
> ```

---

## 💬 예시 입력/출력

### 시나리오 1 — 정상 조건 입력

```
🏡 어떤 매물을 찾으시나요? 마포구에서 월세 보증금 3000에 월 80 이하 투룸 구해줘

📝 입력: 마포구에서 월세 보증금 3000에 월 80 이하 투룸 구해줘

파싱된 조건:
┌──────────────┬─────────┐
│ 항목         │ 값      │
├──────────────┼─────────┤
│ 지역         │ 마포구  │
│ 거래유형     │ 월세    │
│ 최대보증금   │ 3000    │
│ 최대월세     │ 80      │
│ 방종류       │ 투룸    │
└──────────────┴─────────┘

📊 1차 검색: 3개 → 필터링 후: 2개

✨ 추천 매물

🏆 1순위 추천: 마포구 공덕동 투룸 (P001)
- 보증금 3,000만원 / 월세 80만원
- 45m², 5호선 공덕역 도보 5분
- 장점: 역세권, 엘리베이터, 남향 채광 우수
- 추천 이유: 예산 조건 딱 맞고 편의시설 풍부

2순위: 마포구 신수동 투룸 (P011)
- 보증금 1,500만원 / 월세 70만원
- 38m², 6호선 광흥창역 도보 7분
- 장점: 한강 인근, 예산보다 저렴
```

### 시나리오 2 — 불충분한 입력 → 재질문

```
🏡 어떤 매물을 찾으시나요? 집 구하고 싶어

🤔 추가 정보 필요: 안녕하세요! 어떤 지역을 희망하시나요? 
                  그리고 월세/전세/매매 중 어떤 방식을 원하세요?
👉 서울 강남구 전세로 찾아줘

(이후 정상 처리)
```

### 시나리오 3 — 조건 매칭 없음

```
🏡 어떤 매물을 찾으시나요? 강남구 매매 1억 이하 아파트

😔 죄송합니다. 입력하신 조건에 맞는 매물을 찾을 수 없습니다.
조건을 조금 완화하시거나 다른 지역/거래유형으로 다시 검색해 보세요.
```

---

## 🔧 사용 Tool 설명

### `search_web` (tools/web_search_tool.py)
- **역할**: Tavily API로 부동산 관련 정보를 **실시간 웹 검색** (네이버 부동산·호갱노노·실거래가 사이트 우선)
- **입력**: `condition` (조건으로 쿼리 자동 생성)
- **출력**: 검색 결과 리스트 `[{title, url, content}, ...]`
- **동작**: `TAVILY_API_KEY` 없으면 조용히 빈 리스트 반환 (매물 생성 흐름 중단 없음)

### `llm_generate_properties` (tools/llm_search_tool.py)
- **역할**: Agent(LLM)가 사용자 조건에 부합하는 현실적인 매물을 실시간 생성
- **입력**: `condition` (위치·거래유형·금액·면적·세대수·주차·역까지 분·방/욕실·층/방향·연식 등)
- **동작**: 내부적으로 `search_web`을 먼저 호출해 웹 검색 결과를 LLM 프롬프트에 주입 → **hallucination 완화**
- **출력**: JSON 매물 리스트 (기본 6개)

### `filter_and_score` (tools/filter_tool.py)
- **역할**: 생성 매물을 전체 조건으로 2차 필터링하고 점수 계산 후 정렬
- **점수 기준**:
  - 가격 조건 충족: +30점
  - 면적 조건 충족: +20점
  - 역세권(도보 7분 이내): +15점
  - 주차 가능: +5점
  - 세대수/구조/방향/연식 등 선택 조건 보너스: 항목당 +5점
  - features 개수 × 5점
- **출력**: 점수 내림차순 상위 5개 매물

### `verify_node` (agent/nodes.py)
- **역할**: 추천 직전에 **가격·유형·지역 3대 필수조건**을 매물별로 재검증
- **검증 항목**:
  - **가격**: `deal_type`별로 `max_deposit`/`max_monthly`/`max_price` 상한 준수 여부
  - **유형**: 사용자가 지정한 `deal_type`(월세/전세/매매), `property_type`(원룸/아파트 등) 정확히 일치
  - **지역**: 사용자 `region` 문자열이 매물의 `region`/`district`/`subway`/`title` 중 하나에 포함
- **동작**:
  - 탈락한 매물은 `filtered_results`에서 제거하고 매물별 ✅/❌ 및 사유 로그 출력
  - **통과 매물이 0건이면 `search`로 되돌아가 매물을 다시 생성** (최대 2회 재시도)
  - 재시도 후에도 통과 매물이 없으면 `recommend`가 조건 완화 제안 메시지 출력
- **출처**: LLM 생성 매물이 조건을 빗겨나갈 가능성을 막는 최종 안전장치

---

## 📁 프로젝트 구조

```
final-ax6/
├── main.py                  # 진입점
├── requirements.txt
├── README.md
├── .env.example
├── agent/
│   ├── __init__.py
│   ├── state.py             # AgentState / UserCondition TypedDict
│   ├── graph.py             # LangGraph 워크플로우
│   └── nodes.py             # 6개 노드 로직 (parse / validate / clarify / search / verify / recommend)
├── tools/
│   ├── __init__.py
│   ├── web_search_tool.py   # Tavily 웹 검색 Tool
│   ├── llm_search_tool.py   # LLM 매물 생성 Tool (웹 검색 결과 주입)
│   ├── search_tool.py       # (폴백) Mock 검색 Tool
│   └── filter_tool.py       # 조건 필터링 Tool
└── data/
    └── mock_properties.json # (폴백) Mock 매물 데이터 (18개)
```
