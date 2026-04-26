import json
import re
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import traceable

from tools.web_search_tool import search_web, format_web_context


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", temperature=0.4)


@traceable(name="llm_generate_properties")
def llm_generate_properties(condition: dict, count: int = 6, relaxed: bool = False) -> List[dict]:
    """
    LLM을 이용해 사용자 조건에 맞는 부동산 매물 목록을 생성합니다.
    Tavily 웹 검색 결과를 컨텍스트로 주입합니다.

    Args:
        condition: 사용자 조건 dict
        count:     생성할 매물 개수
        relaxed:   True면 LLM에 조건 완화 지시를 추가 (verify 재시도 시 사용)
    """
    llm = _get_llm()

    web_results = search_web(condition, max_results=5)
    web_context = format_web_context(web_results)

    if web_results:
        print(f"[웹 검색] {len(web_results)}건 수집")
    else:
        print("[웹 검색] 결과 없음 → LLM 단독 생성")

    condition_text = json.dumps(condition, ensure_ascii=False, indent=2)

    relaxed_instruction = (
        "\n\n⚠️ 완화 모드: 이전 검색에서 조건에 맞는 매물이 부족했습니다. "
        "핵심 조건(지역, 거래유형, 주요 금액)은 유지하되 "
        "세대수, 건물 구조, 층, 방향 등 부가 조건은 조금 유연하게 적용해 다양한 매물을 생성해주세요."
        if relaxed else ""
    )

    system_prompt = (
        "당신은 대한민국 부동산 전문 상담사입니다. "
        f"사용자의 검색 조건에 맞는 현실적이고 다양한 매물 {count}개를 생성해주세요.\n\n"
        "규칙:\n"
        "- '실시간 웹 검색 결과'가 있으면 실제 단지명/지역/시세를 우선 참조.\n"
        "- 실제 존재하는 지역·지하철역 이름을 사용하세요.\n"
        "- 가격은 해당 지역의 실제 시세 수준을 반영 (단위: 만원).\n"
        "- 매물 유형/층/방향/연식/주차 여부 등은 매물마다 다양하게.\n"
        "- 사용자 조건(지역/거래유형/금액/면적/주차 등)에 가능한 한 부합하도록.\n"
        "- JSON 배열로만 응답. 코드블록/설명/주석 금지.\n\n"
        "🚨 price 필드 규칙 (절대 0으로 두지 말 것):\n"
        "- 월세: {\"deposit\": 보증금(만원), \"monthly\": 월세(만원)}\n"
        "- 전세: {\"deposit\": 전세보증금(만원), \"monthly\": 0}\n"
        "- 매매: {\"deposit\": 매매가(만원), \"monthly\": 0}\n\n"
        "각 매물의 필수 필드:\n"
        "[{\n"
        '  "id": "L001"~"L0NN",\n'
        '  "title": "매물 한줄 제목",\n'
        '  "region": "시·도 구",\n'
        '  "district": "동 이름",\n'
        '  "type": "원룸/투룸/쓰리룸/아파트/오피스텔",\n'
        '  "deal_type": "월세/전세/매매",\n'
        '  "price": {"deposit": 숫자, "monthly": 숫자},\n'
        '  "area_m2": 숫자,\n'
        '  "floor": 숫자, "total_floors": 숫자,\n'
        '  "households": 숫자,\n'
        '  "parking": true/false,\n'
        '  "building_structure": "계단식/복도식",\n'
        '  "subway": "N호선 XX역 도보 N분",\n'
        '  "subway_minutes": 숫자,\n'
        '  "rooms": 숫자, "bathrooms": 숫자,\n'
        '  "direction": "남향/동향/서향/북향/남동향/남서향",\n'
        '  "built_year": 숫자,\n'
        '  "features": ["주차가능", "엘리베이터", ...],\n'
        '  "description": "한줄 설명",\n'
        '  "score": 0\n'
        "}]"
        + relaxed_instruction
    )

    user_parts = [f"사용자 조건:\n{condition_text}"]
    if web_context:
        user_parts.append(web_context)
    user_parts.append(f"위 조건에 맞는 매물 {count}개 JSON 배열로 생성.")
    user_message = "\n\n".join(user_parts)

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ])
    text = response.content.strip()
    text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1:
        text = text[start: end + 1]
    properties = json.loads(text)
    if not isinstance(properties, list):
        return []
    return properties