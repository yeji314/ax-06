import json
import re
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from tools.web_search_tool import search_web, format_web_context


def _get_llm() -> ChatOpenAI:
    # 약간의 다양성을 위해 temperature를 살짝 올림
    return ChatOpenAI(model="gpt-4o-mini", temperature=0.4)


def llm_generate_properties(condition: dict, count: int = 6) -> List[dict]:
    """
    LLM을 이용해 사용자 조건에 맞는 현실적인 부동산 매물 목록을 생성합니다.
    가능한 경우 Tavily 웹 검색 결과를 근거로 함께 제공합니다.

    Args:
        condition: 사용자 조건 dict (region, deal_type, max_deposit 등)
        count: 생성할 매물 개수

    Returns:
        매물 dict 리스트 (search_tool과 동일한 스키마)
    """
    llm = _get_llm()

    # 1) 웹 검색으로 실시간 시세·단지 정보 수집
    web_results = search_web(condition, max_results=5)
    web_context = format_web_context(web_results)

    if web_results:
        print(f"[🌐 웹 검색 {len(web_results)}건 수집 → LLM 프롬프트 주입]")
        for r in web_results:
            print(f"  - {r['title'][:60]}")
    else:
        print("[🌐 웹 검색 결과 없음 (TAVILY_API_KEY 미설정 또는 실패) → LLM 단독 생성]")

    condition_text = json.dumps(condition, ensure_ascii=False, indent=2)

    system_prompt = (
        "당신은 대한민국 부동산 전문 상담사입니다. "
        f"사용자의 검색 조건에 맞는 현실적이고 다양한 매물 {count}개를 생성해주세요.\n\n"
        "규칙:\n"
        "- 아래 '실시간 웹 검색 결과'가 있으면 거기 나오는 실제 단지명/지역/시세를 우선 참조.\n"
        "- 실제 존재하는 지역·지하철역 이름을 사용하세요.\n"
        "- 가격은 해당 지역의 실제 시세 수준을 반영 (단위: 만원).\n"
        "- 매물 유형/층/방향/연식/주차 여부 등은 매물마다 다양하게.\n"
        "- 사용자 조건(지역/거래유형/금액/면적/주차 등)에 가능한 한 부합하도록.\n"
        "- JSON 배열로만 응답. 코드블록/설명/주석 금지.\n\n"
        "각 매물의 필수 필드:\n"
        "[{\n"
        '  "id": "L001"~"L0NN",\n'
        '  "title": "매물 한줄 제목",\n'
        '  "region": "시·도 구",\n'
        '  "district": "동 이름",\n'
        '  "type": "원룸/투룸/쓰리룸/아파트/오피스텔",\n'
        '  "deal_type": "월세/전세/매매",\n'
        '  "price": {"deposit": 숫자, "monthly": 숫자(매매/전세면 0)},\n'
        '  "area_m2": 숫자,\n'
        '  "floor": 숫자, "total_floors": 숫자,\n'
        '  "households": 숫자,\n'
        '  "parking": true/false,\n'
        '  "building_structure": "계단식/복도식",\n'
        '  "subway": "N호선 XX역 도보 N분",\n'
        '  "subway_minutes": 숫자,\n'
        '  "rooms": 숫자, "bathrooms": 숫자,\n'
        '  "direction": "남향/동향/서향/북향/남동향/남서향",\n'
        '  "built_year": 숫자(연도),\n'
        '  "features": ["주차가능", "엘리베이터", ...],\n'
        '  "description": "한줄 설명",\n'
        '  "score": 0\n'
        "}]"
    )

    user_message_parts = [f"사용자 조건:\n{condition_text}"]
    if web_context:
        user_message_parts.append(web_context)
    user_message_parts.append(f"위 조건에 맞는 매물 {count}개 JSON 배열로 생성.")
    user_message = "\n\n".join(user_message_parts)

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        text = response.content.strip()
        # 코드블록 제거
        text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
        # JSON 배열 시작지점 이후만
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start : end + 1]
        properties = json.loads(text)
        if not isinstance(properties, list):
            return []
        return properties
    except Exception as e:
        print(f"[LLM 매물 생성 오류] {e}")
        return []
