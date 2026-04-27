# 🏠 부동산 추천 Agent

> LangGraph 기반 부동산 상담사형 AI Agent · **국토부 실거래가 실데이터 기반**

---

## 1. 소개

### 저희 조는 **부동산 매매 조회 Agent**를 만들었습니다.

복잡한 부동산 검색을 **마치 상담사와 대화하듯**
자연어로 조건을 말하면 실제 거래 데이터 기반으로 매물을 찾아주는 Agent.

```
"학군 좋은 서울 아파트 전세 5억 알려줘"
         ↓
    Agent가 알아서
    - 자연어를 조건으로 파싱하고
    - 부족한 정보는 되묻고
    - 국토부 실거래가 API로 조회하고
    - 검증 통과 시에만 추천
```

- **팀원**: AX 6조 · **트랙**: ① Workflow
- **기술 스택**:
  - **LangGraph** (StateGraph + MemorySaver) · **LangChain** · **Pydantic** structured output
  - **OpenAI GPT-4o-mini** · **국토교통부 실거래가 API** · **Tavily** 웹 검색 · **LangSmith** 추적
  - **FastAPI** + **Streamlit** + **Rich CLI** 3가지 인터페이스
- **목표**: 부동산 초심자도 자연어 한 줄로 진짜 매물을 받아볼 수 있게

---

## 2. 개발 의도

### 💭 부동산을 사겠다고 결심하는 순간

처음엔 **가격**과 **위치** 두 가지만 생각하지만…

```
  가격 → "헉, 비싸다"
     ↓
  세세한 조건 → "세대수? 주차? 계단식/복도식? 연식?"
     ↓
  공부해야 할 게 너무 많음 😵
     ↓
  포기하거나 막막해짐
```

### 🎯 그래서 만들었습니다

> **"부동산 상담사처럼, 질문하면 대답해주는 Agent"**

- 부동산 시장 진입 장벽을 낮춘다
- 복잡한 필터 대신 **자연어 대화**로 매물을 탐색
- **불충분한 질문 → 되묻기**로 초심자도 자연스럽게 조건을 구체화
- **검증 단계**로 엉뚱한 추천을 방지
- **실거래 데이터** 기반 — LLM hallucination 최소화

---

## 3-A. 구현 요소 — State 정의

```python
class UserCondition(TypedDict, total=False):
    """매물 스펙 (16개 필드)"""
    region · deal_type · max_deposit · max_monthly · max_price
    min_area · property_type · min_households · parking_required
    building_structure · max_subway_minutes · min_rooms · min_bathrooms
    preferred_floor · direction · max_building_age

class UserLifestyle(TypedDict, total=False):
    """생활권·분위기 — 학군·한강·카페 같은 목적성 키워드"""
    activities · amenities · atmosphere · raw_keywords

class AgentState(TypedDict):
    user_input · condition · lifestyle · is_valid · error_message
    clarify_question     # 멀티턴: 부족 시 프론트로 반환
    search_results · filtered_results · recommendations
    retry_count · verify_retry_count · relaxed
    messages
```

| 구분 | 필드 |
|------|------|
| **필수 (validate)** | 지역 · 거래유형(매매/전세/월세) · 가격 |
| 선택 (filter) | 방종류 · 면적 · 세대수 · 주차 · 구조 · 역까지 분 · 방/욕실수 · 층 · 방향 · 연식 |
| 라이프스타일 | 학군 · 한강 · 카페 · 공원 · 대학 · 조용함 · 런닝 · 등 |

---

## 3-B. State Diagram

```
              ┌──────────────────────┐
              │       [START]        │
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │   parse_condition    │  Pydantic structured output
              │                      │  + 억 단위 자동 교정
              │                      │  + 멀티턴 condition 머지
              └──────────┬───────────┘
                         ▼
              ┌──────────────────────┐
              │      validate        │  지역·유형·가격 3대 필수
              └──────────┬───────────┘
                         │
            ┌────────────┴────────────┐
       조건 부족                  조건 충족
            ▼                        ▼
   ┌────────────────┐       ┌─────────────────────────────┐
   │   clarify      │       │           search             │
   │ 질문만 state에  │       │  ① 광역 region + 라이프스타일 │
   │ 담고 그래프 종료 │       │  ② 인접 구 점진 확장          │
   └────────────────┘       │  ③ 국토부 실거래가 API 호출   │
       ▲                    │  ④ filter_and_score          │
       │  (다음 invoke)     └──────────────┬──────────────┘
       │  MemorySaver로                    ▼
       │  이전 condition 유지     ┌────────────────┐
       │                          │     verify     │  가격·유형·지역 재검증
       │                          └────────┬───────┘
       │                                   │
       │                    ┌──────────────┴─────────────┐
       │               통과 0건 & retry<2          통과 ≥ 1건
       │                    ▼                            ▼
       │              search 재실행            ┌────────────────┐
       │           (인접 구 +1, relaxed=True)  │   recommend    │
       │                                        │  + Tavily 동네  │
       │                                        │   정보 컨텍스트 │
       │                                        └────────┬───────┘
       │                                                 ▼
       └─────────  사용자 답변 →                       [END]
                  parse 재진입
```

**두 개의 자기복원 루프**
- ① **clarify 루프** (조건 부족 시): 사용자 답변 → MemorySaver가 이전 조건 누적
- ② **verify 재검색 루프** (결과 부족 시): 인접 구 확장 + 소프트 조건 완화

---

## 3-C. 노드별 역할

| # | 노드 | 핵심 동작 |
|---|------|----------|
| 1 | **parse_condition** | Pydantic `with_structured_output` → JSON 강제 / 억 단위 오류 교정 / 이전 condition 머지 |
| 2 | **validate** | 지역·거래유형·가격 3대 필수 / `retry_count >= 2` 안전 통과 |
| 3 | **clarify** | 부족 항목 + 예시로 질문 생성 → state에 저장 후 그래프 종료 (multi-turn) |
| 4 | **search_and_filter** | 광역→대표구·인접구 확장 → MOLIT 실거래가 API → filter & score |
| 5 | **verify** | 가격·유형·지역 재검증 / 0건 시 search로 자기복원 (최대 2회) |
| 6 | **recommend** | 매물 + Tavily 동네 정보로 자연어 추천 멘트 생성 |

### 핵심 설계 철학

> "Agent는 단순 실행자가 아니라, **스스로 판단하고 돌아갈 줄 아는 존재**"

- **질문 평가 → 단계 회귀**: clarify 루프 (조건 부족 시 parse로 복귀)
- **결과 평가 → 단계 회귀**: verify 루프 (검증 실패 시 search로 복귀)
- **세션 상태 보존**: MemorySaver checkpointer + thread_id

---

## 3-D. Tool 설계

### 🏛️ `tools/molit_api.py` — 국토교통부 실거래가 API ⭐ 메인 데이터 소스

- 아파트/빌라 × 매매/전월세 **4개 엔드포인트** 호출
- 법정동 코드(LAWD_CD) 매핑 내장 — 서울 25개 구 + 경기 주요 시
- **랜드마크/역명 → 인접 구** 폴백 매핑 — `서울역→중구`, `홍대→마포구`, `잠실→송파구` 등 50개 이상
- **NEIGHBOR_GU 인접 그래프** — verify 재시도 시 점진 확장
- **LIFESTYLE_KEYWORD_TO_GU** — `학군→강남·서초·양천·노원·송파`, `한강→용산·마포·영등포·광진·성동` 등
- 최근 3개월 페이지네이션 (페이지당 1000건 × 5페이지)

### 🌐 `tools/web_search_tool.py` — Tavily 동네 정보

- **추천 멘트 생성 시 컨텍스트로만 사용** (매물 데이터로는 사용 X)
- `include_domains=[land.naver.com, hogangnono.com, rtms.molit.go.kr ...]` 신뢰 도메인 우선
- 키 없으면 빈 결과 → 흐름 안 끊김 (graceful fallback)

### 🧮 `tools/filter_tool.py` — 매물 필터링·점수화

- 하드 필터: 가격·면적·거래유형·매물유형(MOLIT 카테고리 매핑)·세대수·주차·구조·역세권·층·방향·연식
- 점수: 가격(+30) · 면적(+20) · 역세권7분(+15) · 주차(+5) · 옵션 매칭(+5씩) · features×3 · **생활권 보너스(최대+30)**

### 🔍 `agent/nodes.py::verify_node` — 필수조건 재검증

- 추천 직전 **가격·거래유형·지역** 3대 매물별 재검증
- 통과 0건 시 search로 회귀 (최대 2회 + 인접 구 점진 확장 + 소프트 조건 완화)

---

## 3-E. 데이터 흐름 — "광역·키워드 → 실거래"

```
"학군 좋은 서울 아파트 전세 5억"
              │
              ▼ parse (Pydantic)
condition = {region:"서울", deal_type:"전세", max_deposit:50000, ...}
lifestyle = {raw_keywords:"학군"}
              │
              ▼ search (광역 인지)
"서울"은 LAWD_CD 없음 → 라이프스타일 분석
"학군" → 강남·서초·양천 (LIFESTYLE_KEYWORD_TO_GU)
              │
              ▼ MOLIT API 다중 호출
강남구 RTMSDataSvcAptRent 202604/05/06
서초구 RTMSDataSvcAptRent 202604/05/06
양천구 RTMSDataSvcAptRent 202604/05/06
              │
              ▼ filter_and_score
하드 필터 통과 + 점수 정렬 → 상위 5건
              │
              ▼ verify (가격·유형·지역)
3대 필수 미통과 매물 탈락
              │     ↺ 0건이면 인접 구 + 소프트 완화로 재시도
              ▼
recommend (Tavily 동네 정보 + 매물 → LLM 추천 멘트)
```

> **핵심**: LLM은 데이터 소스가 아니라 **인터페이스**
> 입구(자연어 → 조건)와 출구(매물 → 추천 멘트)만 담당

---

## 3-F. 예시 — 정상 시나리오

**입력**
```
🏡 어떤 매물을 찾으시나요? 마포구 월세 보증금 3000 월 80 이하 투룸
```

**파싱·검색 로그**
```
[parse] 조건={region:'마포구', deal_type:'월세', max_deposit:3000, max_monthly:80, property_type:'투룸'}
[search] 실거래 API 조회 (relaxed=False, neighbors=0)
[MOLIT] 아파트 월세 202604 → 142건
[MOLIT] 빌라 월세 202604 → 87건
[MOLIT] 총 689건 (지역: 마포구, 최근 3개월)
[search] 필터 통과 5건
[verify] 5건 → 4건 통과
```

**추천**
```
🏆 1순위: 마포구 도화동 OO아파트
  - 5호선 공덕역 도보 5분 · 보증금 3,000 / 월 80
  - 추천 이유: 예산 완벽 충족, 역세권, 남향, 학원가 인접
```

---

## 3-G. 예시 — 멀티턴 되묻기

**Turn 1**
```
🏡 왕십리 아파트 추천해줘
[parse] 조건={region:'왕십리', property_type:'아파트'}
→ 거래유형·가격 누락 → clarify
🤔 거래 유형(월세/전세/매매)과 예산을 알려주세요.
```

**Turn 2**
```
👉 매매 15억 이하
[parse] 이전 condition 머지 → {region:'왕십리', property_type:'아파트', deal_type:'매매', max_price:150000}
[MOLIT] 랜드마크 매핑: '왕십리' → '성동구' (11200)
[MOLIT] 아파트 매매 202604 → ...
✅ 추천 매물 N건
```

> **MemorySaver checkpointer로 thread_id 단위 세션 보존**
> 한 번에 다 말 안 해도 누적해서 처리

---

## 3-H. 예시 — verify 재검색 (인접 구 확장)

```
🏡 중구 매매 5억 이하 아파트

[search] neighbors=0 → 중구만
[verify] 5건 → 0건 통과 (5억 이하 아파트가 없음)
[verify] 재시도 1회차 예약

[search] neighbors=1 → "중구 용산구" 다중 조회
[MOLIT] 다중 구 동시 조회: ['중구', '용산구']
[MOLIT] 다중 구 합산: 총 110건
[verify] 5건 → 2건 통과 ✅
```

> "결과가 없으면 스스로 인접 지역까지 확장"

---

## 4-A. 회고 — 배운 점

### 💡 핵심 인사이트

> **"부동산은 검색이 아니라 상담이다"**

| 처음 생각 | 실제로 깨달은 것 |
|-----------|-----------------|
| 조건만 주면 필터링하면 끝 | 질문 자체가 불완전한 경우가 대부분 |
| 1-shot LLM 호출로 충분 | State Machine으로 **단계별 평가/회귀**가 핵심 |
| Mock JSON으로 데모 | LLM 단독 생성은 hallucination → **공공 실거래가 API**가 정답 |
| 네이버 부동산 공식 API 필요 | 공식 API 없음 → **국토부 RTMS** + Tavily로 충분히 대체 가능 |
| LLM이 데이터 소스 | LLM은 **인터페이스(파싱·재질문·추천 멘트)** — 데이터는 공공API |
| 한 번에 다 말해야 함 | **멀티턴 대화**가 자연스러움 (MemorySaver) |

### 🏗️ LangGraph의 강점 체감

- 조건부 엣지(`add_conditional_edges`)로 **상황별 분기** 자연스럽게
- 각 노드는 작고 명확 — 디버깅·확장이 쉬움
- `State` 중심 설계로 **모든 단계의 맥락이 투명**
- **MemorySaver checkpointer**로 멀티턴 대화 자동 처리
- **LangSmith @traceable**로 LLM 호출별 trace 가시화

---

## 4-B. 회고 — 추가 개발 방향

### 🚀 확장 계획

**① State 확장 — 다차원 조건**
```
현재: 매물 스펙 + 라이프스타일
다음: 목적(실거주/투자) · 가족 구성 · 출퇴근 동선 · 학군 우선도
```

**② 사용자 의도 파악 고도화**
- "실거주 vs 투자" 같은 **목적 기반 분기** 추가
- 투자 → 수익률·전세가율·거래량 중심
- 실거주 → 학군·편의시설·출퇴근 중심

**③ RAG / 추천 시스템 결합**
```
단순 조회형 Agent  →  개인화 추천형 Agent
  - 과거 대화 이력 기반 선호도 학습
  - 유사 매물 RAG 검색 (실거래가 임베딩)
  - 사용자 페르소나 기반 랭킹
```

**④ 데이터 소스 다각화**
- ✅ 국토부 실거래가 API (구현 완료)
- ✅ Tavily 동네 정보 (구현 완료)
- 🔜 KOSIS 외국인 거주 통계 → "중국인 많은 곳" 같은 질문
- 🔜 학교알리미 API → 정량 학군 점수
- 🔜 카카오 로컬 API → "시청역 30km 반경" 같은 거리 제약

---

## 4-C. 회고 — 아쉬웠던 점

### ⚠️ 한계

**평가 단계의 모호성 판단**
- 질문의 모호함을 **완벽하게 판단하지 못하는 경우** 존재
- 예: "싸게 좋은 집" — 주관적 조건은 파싱 어려움
- 예: "조용한 동네" — 정량화되지 않은 조건

**LLM 파싱의 일관성**
- "중구 아파트"를 `property_type='중아파트'`로 합성하는 등 가끔 오류
- 화이트리스트 + 이전 condition 머지로 방어 중

**MOLIT API의 한계**
- 실거래 **이력**(과거)만 제공, **현재 매물 호가**는 아님
- 시·구 단위 코드만 지원 → 동·역 단위는 자체 매핑으로 보완
- 빌라(연립다세대) 매물은 분류가 거칠어서 원룸/투룸 매핑이 부정확

**재검색 루프의 한계**
- 검증 0건 시 최대 2회 재시도 후 포기
- 근본 원인이 "비현실적 조건"일 때는 루프만으로 해결 불가
- → **조건 완화 제안**으로 우회 중

### 🛠️ 개선 방향
- 모호한 질문 감지 모델 별도 분리
- 검증 실패 원인별 차등 처리 (가격 위반 → 완화 / 지역 위반 → 인접 구)
- 호가 데이터(직방·다방 제휴) 추가 연동
- 빌라 매물 분류 정교화

---

## 🎬 데모

```bash
# CLI
python main.py

# 웹 (FastAPI + Streamlit)
./run.sh
# → http://localhost:8501
```

```
🏡 어떤 매물을 찾으시나요? _
```

**감사합니다! 🙌**

[📂 GitHub: github.com/yeji314/ax-06](https://github.com/yeji314/ax-06)
