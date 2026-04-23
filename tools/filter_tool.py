from datetime import datetime

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


def _floor_band(floor: int, total: int) -> str:
    if not total:
        return ""
    ratio = floor / total
    if ratio <= 1 / 3:
        return "저층"
    if ratio <= 2 / 3:
        return "중층"
    return "고층"


def filter_and_score_raw(properties: list, condition: dict) -> list:
    """Tool 래퍼 없이 직접 호출하는 버전."""
    filtered = []
    current_year = datetime.now().year

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

        # 세대수
        min_households = condition.get("min_households")
        if min_households and prop.get("households", 0) < min_households:
            continue

        # 주차
        parking_required = condition.get("parking_required")
        if parking_required and not prop.get("parking"):
            continue

        # 계단식/복도식
        building_structure = condition.get("building_structure")
        if building_structure and prop.get("building_structure") != building_structure:
            continue

        # 역까지 도보 시간
        max_subway_minutes = condition.get("max_subway_minutes")
        if max_subway_minutes and prop.get("subway_minutes", 99) > max_subway_minutes:
            continue

        # 방/욕실 개수
        min_rooms = condition.get("min_rooms")
        if min_rooms and prop.get("rooms", 0) < min_rooms:
            continue
        min_bathrooms = condition.get("min_bathrooms")
        if min_bathrooms and prop.get("bathrooms", 0) < min_bathrooms:
            continue

        # 층
        preferred_floor = condition.get("preferred_floor")
        if preferred_floor:
            band = _floor_band(prop.get("floor", 0), prop.get("total_floors", 0))
            if band and band != preferred_floor:
                continue

        # 방향
        direction = condition.get("direction")
        if direction and direction not in (prop.get("direction") or ""):
            continue

        # 연식
        max_building_age = condition.get("max_building_age")
        if max_building_age and prop.get("built_year"):
            age = current_year - prop["built_year"]
            if age > max_building_age:
                continue

        # 점수 계산
        # 가격 조건 충족: +30점
        score += 30

        # 면적 조건 충족: +20점
        if min_area and prop.get("area_m2", 0) >= min_area:
            score += 20
        elif not min_area:
            score += 20

        # 역세권(도보 7분 이내): +15점
        if prop.get("subway_minutes", 99) <= 7:
            score += 15

        # 주차 가능: +5점
        if prop.get("parking"):
            score += 5

        # 조건과 매칭되는 추가 항목 보너스
        if min_households and prop.get("households", 0) >= min_households:
            score += 5
        if building_structure and prop.get("building_structure") == building_structure:
            score += 5
        if direction and direction in (prop.get("direction") or ""):
            score += 5
        if max_building_age and prop.get("built_year"):
            age = current_year - prop["built_year"]
            if age <= max_building_age:
                score += 5

        # features 개수 × 5점
        features = prop.get("features", [])
        score += len(features) * 5

        prop["score"] = score
        filtered.append(prop)

    filtered.sort(key=lambda x: x["score"], reverse=True)
    return filtered[:5]
