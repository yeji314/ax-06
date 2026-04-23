import os
from typing import List, Optional

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None


def _build_query(condition: dict) -> str:
    """조건 dict로부터 웹 검색 쿼리 문자열 생성."""
    parts = []

    region = condition.get("region")
    if region:
        parts.append(region)

    deal_type = condition.get("deal_type")
    if deal_type:
        parts.append(deal_type)

    property_type = condition.get("property_type")
    if property_type:
        parts.append(property_type)

    # 예산
    if condition.get("max_price"):
        parts.append(f"{condition['max_price']}만원 이하")
    elif condition.get("max_deposit"):
        parts.append(f"보증금 {condition['max_deposit']}만원 이하")
    if condition.get("max_monthly"):
        parts.append(f"월세 {condition['max_monthly']}만원 이하")

    parts.append("부동산 매물 시세")
    return " ".join(parts)


def search_web(condition: dict, max_results: int = 5) -> List[dict]:
    """
    Tavily로 부동산 관련 정보를 웹에서 검색합니다.

    Args:
        condition: 사용자 조건 dict
        max_results: 가져올 최대 결과 수

    Returns:
        검색 결과 리스트 [{title, url, content}, ...]
        API 키가 없거나 오류가 나면 빈 리스트 반환
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key or TavilyClient is None:
        return []

    query = _build_query(condition)
    try:
        client = TavilyClient(api_key=api_key)
        # 네이버 부동산 관련 도메인 우선
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_domains=[
                "land.naver.com",
                "new.land.naver.com",
                "news.naver.com",
                "zigbang.com",
                "dabangapp.com",
                "rtms.molit.go.kr",
                "hogangnono.com",
            ],
        )
        results = response.get("results", [])
        # 도메인 필터링으로 결과가 부족하면 일반 검색도 수행
        if len(results) < 3:
            fallback = client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
            )
            results = results + fallback.get("results", [])

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:500],  # 너무 길면 잘라냄
            }
            for r in results[:max_results]
        ]
    except Exception as e:
        print(f"[웹 검색 오류] {e}")
        return []


def format_web_context(results: List[dict]) -> str:
    """웹 검색 결과를 LLM 프롬프트용 텍스트로 포맷."""
    if not results:
        return ""

    lines = ["## 실시간 웹 검색 결과 (참고용)"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r['title']}")
        if r.get("url"):
            lines.append(f"    출처: {r['url']}")
        if r.get("content"):
            lines.append(f"    요약: {r['content']}")
    return "\n".join(lines)
