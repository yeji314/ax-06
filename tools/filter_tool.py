from langchain_core.tools import tool


@tool
def filter_and_score(properties: list, condition: dict) -> list:
    """
    매물을 조건에 맞게 필터링하고 점수를 계산합니다.

    Args:
        properties: 검색된 매물 목록
        condition: 사용자 조건 dict

    Returns:
        점수 기준 내림차순 정렬된 매물 목록 (최대 5개)
    """
    return filter_and_score_raw(properties, condition)


def filter_and_score_raw(properties: list, condition: dict) -> list:
    """Tool 래퍼 없이 직접 호출하는 버전."""
    filtered = []

    for p in properties:
        prop = dict(p)
        score = 0
        deal_type = prop.get("deal_type", "")
        price = prop.get("price", {})

        # 가격 조건 필터링 및 점수
        price_ok = True
        if deal_type == "월세":
            max_deposit = condition.get("max_deposit")
            max_monthly = condition.get("max_monthly")
            if max_deposit and price.get("deposit", 0) > max_deposit:
                price_ok = False
            if max_monthly and price.get("monthly", 0) > max_monthly:
                price_ok = False
        elif deal_type == "전세":
            max_deposit = condition.get("max_deposit") or condition.get("max_price")
            if max_deposit and price.get("deposit", 0) > max_deposit:
                price_ok = False
        elif deal_type == "매매":
            max_price = condition.get("max_price") or condition.get("max_deposit")
            if max_price and price.get("deposit", 0) > max_price:
                price_ok = False

        if not price_ok:
            continue

        # 면적 조건 필터링
        min_area = condition.get("min_area")
        if min_area and prop.get("area_m2", 0) < min_area:
            continue

        # 점수 계산
        # 가격 조건 충족: +30점
        score += 30

        # 면적 조건 충족: +20점
        if min_area and prop.get("area_m2", 0) >= min_area:
            score += 20
        elif not min_area:
            score += 20

        # 지하철 역세권 키워드 포함: +15점
        subway = prop.get("subway", "")
        if "도보" in subway and any(
            m in subway for m in ["1분", "2분", "3분", "4분", "5분", "6분", "7분"]
        ):
            score += 15

        # features 개수 × 5점
        features = prop.get("features", [])
        score += len(features) * 5

        prop["score"] = score
        filtered.append(prop)

    filtered.sort(key=lambda x: x["score"], reverse=True)
    return filtered[:5]
