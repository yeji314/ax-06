import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")
warnings.filterwarnings("ignore", message=".*LibreSSL.*")

import uuid
import streamlit as st
import requests

API_URL = "http://localhost:8000"

st.set_page_config(
    page_title="홈즈 — 내 집 찾기",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* ── 폰트 및 기본 초기화 ── */
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.8/dist/web/static/pretendard.css');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body, [class*="css"], .stApp {
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif !important;
    background-color: #F8F9FA !important; /* 깔끔한 오프화이트 배경 */
    color: #111827;
}

/* ── Streamlit 기본 UI 숨김 및 컨테이너 조정 ── */
.block-container { 
    padding: 2rem 1rem !important; 
    max-width: 768px !important; /* 모바일/웹 모두 집중도 높은 너비 */
    margin: 0 auto;
}
header[data-testid="stHeader"], .stDeployButton, #MainMenu, footer, section[data-testid="stSidebar"] { 
    display: none !important; 
}

/* ── 헤더(히어로) 섹션 ── */
.hero {
    text-align: center;
    padding: 3rem 0 2.5rem;
}
.hero-title {
    font-size: 2.2rem;
    font-weight: 800;
    color: #111827;
    line-height: 1.3;
    letter-spacing: -0.02em;
    margin-bottom: 0.75rem;
}
.hero-title .accent { color: #3182F6; } /* 신뢰감을 주는 모던 블루 */
.hero-sub {
    font-size: 1rem;
    font-weight: 400;
    color: #6B7280;
}

/* ── 칩(예시) ── */
.chips {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.5rem;
    margin-top: 1.5rem;
}
.chip {
    background: #F3F4F6;
    border-radius: 100px;
    padding: 0.4rem 0.9rem;
    font-size: 0.85rem;
    color: #4B5563;
    font-weight: 500;
    cursor: default;
    transition: all 0.2s ease;
}
.chip:hover { background: #E5E7EB; color: #111827; }

/* ── 입력 폼 (검색창) ── */
.search-section {
    margin-bottom: 3rem;
}
.stTextInput > div > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
.stTextInput input {
    background: #FFFFFF !important;
    border: 1px solid #E5E7EB !important;
    border-radius: 16px !important;
    color: #111827 !important;
    font-size: 1rem !important;
    font-weight: 500 !important;
    padding: 1.2rem 1.5rem !important;
    box-shadow: 0 4px 20px rgba(0,0,0,0.03) !important;
    transition: all 0.2s ease !important;
}
.stTextInput input::placeholder { color: #9CA3AF !important; font-weight: 400 !important; }
.stTextInput input:focus {
    border-color: #3182F6 !important;
    box-shadow: 0 0 0 4px rgba(49, 130, 246, 0.1) !important;
}
label[data-testid="stWidgetLabel"] { display: none !important; }

/* 폼 제출 버튼 */
[data-testid="stFormSubmitButton"] button {
    background: #3182F6 !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 16px !important;
    font-size: 1rem !important;
    font-weight: 600 !important;
    padding: 1.2rem !important;
    width: 100% !important;
    height: 100% !important;
    box-shadow: 0 4px 14px rgba(49, 130, 246, 0.2) !important;
    transition: transform 0.1s, background 0.2s !important;
}
[data-testid="stFormSubmitButton"] button:hover { 
    background: #2563EB !important; 
    transform: translateY(-1px);
}
[data-testid="stFormSubmitButton"] button:active {
    transform: translateY(1px);
}

/* ── 결과 요약 메트릭 ── */
.metrics-container {
    display: flex;
    gap: 1rem;
    margin-bottom: 2rem;
}
.metric-box {
    flex: 1;
    background: #FFFFFF;
    border-radius: 20px;
    padding: 1.5rem;
    box-shadow: 0 4px 24px rgba(0,0,0,0.02);
    text-align: center;
}
.metric-value {
    font-size: 1.8rem;
    font-weight: 800;
    color: #111827;
    line-height: 1.2;
}
.metric-label {
    font-size: 0.85rem;
    font-weight: 500;
    color: #6B7280;
    margin-top: 0.25rem;
}

/* ── 조건 태그 ── */
.cond-wrap { margin-bottom: 2.5rem; }
.cond-label {
    font-size: 0.85rem;
    font-weight: 600;
    color: #4B5563;
    margin-bottom: 0.75rem;
}
.cond-tags { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.cond-tag {
    background: #EFF6FF;
    color: #2563EB;
    border-radius: 8px;
    padding: 0.4rem 0.8rem;
    font-size: 0.85rem;
    font-weight: 600;
}

/* ── 매물 카드 (플랫 & 미니멀) ── */
.card-list { display: flex; flex-direction: column; gap: 1rem; margin-bottom: 3rem; }
.item-card {
    background: #FFFFFF;
    border-radius: 24px;
    padding: 1.8rem;
    box-shadow: 0 4px 24px rgba(0,0,0,0.02);
    display: flex;
    flex-direction: column;
    gap: 1rem;
    transition: box-shadow 0.2s ease;
}
.item-card:hover {
    box-shadow: 0 12px 32px rgba(0,0,0,0.06);
}
.card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
}
.card-price {
    font-size: 1.5rem;
    font-weight: 800;
    color: #111827;
    letter-spacing: -0.02em;
}
.card-score {
    background: #F3F4F6;
    color: #4B5563;
    font-size: 0.8rem;
    font-weight: 700;
    padding: 0.3rem 0.7rem;
    border-radius: 100px;
}
.card-score.high { background: #EFF6FF; color: #3182F6; }
.card-title {
    font-size: 1.1rem;
    font-weight: 600;
    color: #374151;
    margin-bottom: 0.2rem;
}
.card-loc {
    font-size: 0.9rem;
    color: #6B7280;
}
.card-tags { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }
.card-tag {
    background: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 6px;
    padding: 0.25rem 0.6rem;
    font-size: 0.8rem;
    color: #4B5563;
    font-weight: 500;
}
.card-tag.highlight {
    background: #F0FDF4;
    border-color: #BBF7D0;
    color: #16A34A;
}

/* ── AI 추천 분석 카드 ── */
.ai-insight {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-left: 4px solid #3182F6;
    border-radius: 16px;
    padding: 1.8rem;
}
.ai-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 1rem;
}
.ai-icon {
    font-size: 1.2rem;
}
.ai-title { 
    font-size: 1rem; 
    font-weight: 700; 
    color: #111827; 
}
.ai-text {
    font-size: 0.95rem;
    color: #4B5563;
    line-height: 1.6;
    white-space: pre-wrap;
}

/* ── 상태 안내 (Empty / Clarify) ── */
.empty-state {
    text-align: center;
    padding: 4rem 0;
}
.empty-icon { font-size: 3rem; margin-bottom: 1rem; color: #D1D5DB; }
.empty-title { font-size: 1.25rem; font-weight: 700; color: #374151; margin-bottom: 0.5rem; }
.empty-sub { font-size: 0.95rem; color: #6B7280; line-height: 1.5; }

.clarify-box {
    background: #FFFFFF;
    border-radius: 16px;
    padding: 1.8rem;
    margin-bottom: 1.5rem;
    text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.02);
}
.clarify-text { font-size: 1.1rem; font-weight: 600; color: #111827; margin-bottom: 0.5rem; }

/* ── 폼 기본 여백 제거 ── */
.stForm, [data-testid="stForm"] { background: transparent !important; border: none !important; padding: 0 !important; }
</style>
""", unsafe_allow_html=True)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def render_cond_tags(condition: dict) -> str:
    parts = []
    label_map = {
        "region": lambda v: f"{v}",
        "deal_type": lambda v: f"{v}",
        "max_deposit": lambda v: f"보증금 {v:,}만",
        "max_monthly": lambda v: f"월세 {v:,}만",
        "max_price": lambda v: f"매매가 {v:,}만",
        "min_area": lambda v: f"면적 {v}㎡↑",
        "property_type": lambda v: f"{v}",
        "parking_required": lambda v: "주차 필수" if v else None,
        "max_subway_minutes": lambda v: f"역 {v}분 이내",
        "min_rooms": lambda v: f"방 {v}개 이상",
        "max_building_age": lambda v: f"{v}년 이내 연식",
    }
    for key, fn in label_map.items():
        val = condition.get(key)
        if val is not None:
            text = fn(val)
            if text:
                parts.append(f'<span class="cond-tag">{text}</span>')
    return "".join(parts) or '<span style="color:#9CA3AF;font-size:.85rem;font-weight:500;">파싱된 조건 없음</span>'


def fmt_price(prop: dict) -> str:
    price = prop.get("price", {})
    dt = prop.get("deal_type", "")
    d, m = price.get("deposit", 0), price.get("monthly", 0)
    def f(n):
        if n >= 10000:
            e, r = divmod(n, 10000)
            return f"{e}억 {r:,}만" if r else f"{e}억"
        return f"{n:,}만"
    if dt == "월세": return f"보 {f(d)} / 월 {m:,}"
    if dt == "전세": return f"전세 {f(d)}"
    if dt == "매매": return f"매매 {f(d)}"
    return str(d)


def render_cards(results: list) -> str:
    html = '<div class="card-list">'
    for i, p in enumerate(results, 1):
        score = p.get("score", 0)
        score_cls = "high" if score >= 60 else ""

        tags = []
        if p.get("area_m2"):    tags.append(("", f"{p['area_m2']}㎡"))
        if p.get("subway_minutes"):
            mins = p["subway_minutes"]
            cls = "highlight" if mins <= 5 else ""
            tags.append((cls, f"역 {mins}분"))
        if p.get("floor"):      tags.append(("", f"{p['floor']}층"))
        if p.get("direction"):  tags.append(("", p["direction"]))
        if p.get("built_year"): tags.append(("", f"{p['built_year']}년"))
        if p.get("parking"):    tags.append(("highlight", "주차 가능"))
        if p.get("households"): tags.append(("", f"{p['households']}세대"))

        tags_html = "".join(
            f'<span class="card-tag {cls}">{t}</span>' for cls, t in tags
        )
        loc = f"{p.get('region','')} {p.get('district','')}".strip()

        html += f"""
        <div class="item-card">
            <div class="card-header">
                <div class="card-price">{fmt_price(p)}</div>
                <div class="card-score {score_cls}">적합도 {score}점</div>
            </div>
            <div>
                <div class="card-title">{p.get('title','')}</div>
                <div class="card-loc">{loc}</div>
            </div>
            <div class="card-tags">{tags_html}</div>
        </div>"""
    html += "</div>"
    return html


def call_api(user_input: str) -> dict:
    # 세션 ID를 함께 전송
    payload = {
        "user_input": user_input,
        "thread_id": st.session_state.thread_id
    }
    resp = requests.post(f"{API_URL}/recommend", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


# ── 세션 상태 ────────────────────────────────────────────────────────────────

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4()) # 접속자별 고유 ID 부여

for k, v in {"pending_clarify": False, "clarify_question": "",
             "original_input": "", "result": None}.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── 히어로 섹션 ──────────────────────────────────────────────────────────────

EXAMPLES = [
    "마포구 월세 3000/80 투룸",
    "강남구 전세 2억 역세권 주차가능",
    "송파구 매매 아파트 3억 방 2개",
]
chips = " ".join(f'<span class="chip">{e}</span>' for e in EXAMPLES)

st.markdown(f"""
<div class="hero">
    <div class="hero-title">원하는 조건으로<br><span class="accent">완벽한 내 집 찾기</span></div>
    <div class="hero-sub">대화하듯 편하게 조건을 입력해 보세요.</div>
    <div class="chips">{chips}</div>
</div>
""", unsafe_allow_html=True)


# ── 검색 폼 ──────────────────────────────────────────────────────────────────

st.markdown('<div class="search-section">', unsafe_allow_html=True)

if not st.session_state.pending_clarify:
    with st.form("search_form", clear_on_submit=False):
        col_input, col_btn = st.columns([4, 1])
        with col_input:
            user_input = st.text_input(
                label="input",
                placeholder="예) 마포구 월세 3000/80 이하 투룸 찾아줘",
                label_visibility="collapsed",
                key="search_input",
            )
        with col_btn:
            submitted = st.form_submit_button("검색", use_container_width=True)

    if submitted:
        if not user_input.strip():
            st.warning("검색 조건을 입력해 주세요.")
        else:
            with st.spinner("최적의 매물을 분석 중입니다..."):
                try:
                    result = call_api(user_input)
                    st.session_state.result = result
                except requests.exceptions.ConnectionError:
                    st.error("서버에 연결할 수 없습니다. 백엔드 서버를 확인해 주세요.")
                    st.stop()
                except Exception as e:
                    st.error(f"오류가 발생했습니다: {e}")
                    st.stop()

            if result.get("status") == "needs_clarification":
                st.session_state.pending_clarify = True
                st.session_state.clarify_question = result["question"]
                st.session_state.original_input = result["original_input"]
                st.rerun()

else:
    st.markdown(f"""
    <div class="clarify-box">
        <div class="clarify-text">{st.session_state.clarify_question}</div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("clarify_form", clear_on_submit=False):
        col_input, col_btn = st.columns([4, 1])
        with col_input:
            clarify_answer = st.text_input(
                key="clarify_input",
                label="clarify",
                placeholder="답변을 입력해 주세요",
                label_visibility="collapsed",
            )
        with col_btn:
            send_clicked = st.form_submit_button("확인", use_container_width=True)

    if st.button("처음으로 돌아가기", use_container_width=True):
        st.session_state.pending_clarify = False
        st.session_state.result = None
        st.rerun()

    if send_clicked:
        if not clarify_answer.strip():
            st.warning("내용을 입력해 주세요.")
        else:
            with st.spinner("다시 분석 중입니다..."):
                try:
                    result = call_api(st.session_state.original_input, clarify_answer=clarify_answer)
                    st.session_state.result = result
                    st.session_state.pending_clarify = False
                except Exception as e:
                    st.error(f"오류: {e}")
                    st.stop()

            if result.get("status") == "needs_clarification":
                st.session_state.pending_clarify = True
                st.session_state.clarify_question = result["question"]
                st.session_state.original_input = f"{st.session_state.original_input} {clarify_answer}"
                st.rerun()

st.markdown("</div>", unsafe_allow_html=True)


# ── 검색 결과 ────────────────────────────────────────────────────────────────

result = st.session_state.result

if result and result.get("status") == "ok":
    
    if result.get("error_message"):
        st.warning(result["error_message"])

    # 요약 메트릭
    st.markdown(f"""
    <div class="metrics-container">
        <div class="metric-box">
            <div class="metric-value">{result['search_count']}</div>
            <div class="metric-label">분석된 매물</div>
        </div>
        <div class="metric-box">
            <div class="metric-value">{result['filtered_count']}</div>
            <div class="metric-label">조건 부합</div>
        </div>
        <div class="metric-box">
            <div class="metric-value" style="color: #3182F6;">{len(result['filtered_results'])}</div>
            <div class="metric-label">최종 추천</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 검색 조건 태그
    st.markdown(f"""
    <div class="cond-wrap">
        <div class="cond-label">적용된 조건</div>
        <div class="cond-tags">{render_cond_tags(result.get("condition", {}))}</div>
    </div>
    """, unsafe_allow_html=True)

    # 매물 리스트
    filtered = result.get("filtered_results", [])
    if filtered:
        st.markdown(render_cards(filtered), unsafe_allow_html=True)
    else:
        st.info("해당 조건에 완벽히 일치하는 매물이 없습니다. 조건을 조금 수정해 보시길 권장합니다.")

    # AI 분석 의견
    rec = result.get("recommendations", "").strip()
    if rec:
        st.markdown(f"""
        <div class="ai-insight">
            <div class="ai-header">
                <span class="ai-icon">💡</span>
                <span class="ai-title">AI 인사이트</span>
            </div>
            <div class="ai-text">{rec}</div>
        </div>
        """, unsafe_allow_html=True)

elif not result and not st.session_state.pending_clarify:
    # 빈 화면 (초기 상태)
    st.markdown("""
    <div class="empty-state">
        <div class="empty-icon">🛋️</div>
        <div class="empty-title">어떤 공간을 찾고 계신가요?</div>
        <div class="empty-sub">지역, 예산, 방 개수 등 원하시는 조건을 입력하시면<br>가장 적합한 매물을 선별해 드립니다.</div>
    </div>
    """, unsafe_allow_html=True)