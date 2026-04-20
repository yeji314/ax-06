import json
import os
from langchain_core.tools import tool


DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mock_properties.json")


def _load_properties() -> list:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


@tool
def search_properties(
    region: str = None,
    deal_type: str = None,
    property_type: str = None,
) -> list:
    """
    부동산 매물을 검색합니다.

    Args:
        region: 희망 지역 (예: "마포구", "강남")
        deal_type: 거래 유형 ("월세", "전세", "매매")
        property_type: 방 종류 ("원룸", "투룸", "아파트" 등)

    Returns:
        매물 목록 (list of dict)
    """
    properties = _load_properties()
    results = []

    for p in properties:
        if region and region not in p["region"] and region not in p["district"]:
            continue
        if deal_type and p["deal_type"] != deal_type:
            continue
        if property_type and p["type"] != property_type:
            continue
        results.append(p)

    return results


def search_properties_raw(
    region: str = None,
    deal_type: str = None,
    property_type: str = None,
) -> list:
    """Tool 래퍼 없이 직접 호출하는 버전."""
    properties = _load_properties()
    results = []

    for p in properties:
        if region and region not in p["region"] and region not in p["district"]:
            continue
        if deal_type and p["deal_type"] != deal_type:
            continue
        if property_type and p["type"] != property_type:
            continue
        results.append(p)

    return results
