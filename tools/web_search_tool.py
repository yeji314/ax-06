"""
Tavily 웹 검색 — 추천 코멘트 생성 시 동네 정보 컨텍스트 수집용
(매물 생성에는 사용하지 않음)
"""

import os
from typing import List

try:
    from tavily import TavilyClient
    _TAVILY_OK = True
except ImportError:
    _TAVILY_OK = False

PROPERTY_DOMAINS = [
    "land.naver.com", "new.land.naver.com",
    "zigbang.com", "dabangapp.com",
    "hogangnono.com", "rtms.molit.go.kr",
]


def search_neighborhood(region: str, lifestyle_keywords: str = "") -> List[dict]:
    """
    동네 분위기·생활편의 정보를 웹에서 검색합니다.
    추천 코멘트 생성 시 컨텍스트로 활용합니다.

    Args:
        region:             지역명 (예: "마포구")
        lifestyle_keywords: 생활권 키워드 (예: "런닝 공원")

    Returns:
        [{title, url, content}, ...] — 오류 시 []
    """
    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key or not _TAVILY_OK:
        return []

    query = f"{region} {lifestyle_keywords} 동네 정보 생활환경".strip()

    try:
        client   = TavilyClient(api_key=api_key)
        response = client.search(query=query, search_depth="basic", max_results=4)
        results  = response.get("results", [])
        return [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", "")[:400],
            }
            for r in results
        ]
    except Exception as e:
        print(f"[웹 검색 오류] {e}")
        return []


def format_web_context(results: List[dict]) -> str:
    """검색 결과 → LLM 프롬프트용 텍스트"""
    if not results:
        return ""
    lines = ["## 동네 정보 (참고용)"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r['title']}")
        if r.get("content"): lines.append(f"    {r['content']}")
    return "\n".join(lines)