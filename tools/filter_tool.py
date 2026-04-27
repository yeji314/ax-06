from datetime import datetime


def _floor_band(floor: int, total: int) -> str:
    if not total:
        return ""
    r = floor / total
    if r <= 1 / 3: return "저층"
    if r <= 2 / 3: return "중층"
    return "고층"


def filter_and_score_raw(
    properties: list,
    condition: dict,
    lifestyle: dict = None,
) -> list:
    """
    매물 필터링 + 점수 계산.

    점수 구성:
      가격 조건 충족    +30
      면적 조건 충족    +20
      역세권(7분 이내)  +15
      주차 가능         +5
      소프트 조건 매칭  조건당 +5
      features 수       개당 +3
      생활권 보너스     lifestyle_score × 0.3 (최대 +30)

    Returns:
        점수 내림차순, 최대 5개
    """
    lifestyle    = lifestyle or {}
    has_ls       = bool(lifestyle.get("activities") or lifestyle.get("atmosphere") or lifestyle.get("amenities"))
    current_year = datetime.now().year
    passed       = []

    for p in properties:
        prop      = dict(p)
        deal_type = prop.get("deal_type", "")
        price     = prop.get("price", {})
        deposit   = price.get("deposit", 0)
        monthly   = price.get("monthly", 0)

        # ── 하드 필터 ─────────────────────────────────────────────────────────
        # 거래유형 (deal_type) 필터 — 전세 요청인데 월세 매물 통과하던 버그 수정
        cond_deal = condition.get("deal_type")
        if cond_deal and deal_type != cond_deal:
            continue

        # 매물 유형 (property_type) 필터 — MOLIT 유형 매핑 포함
        cond_prop = condition.get("property_type")
        if cond_prop:
            TYPE_MAP = {
                "원룸":    ["빌라", "오피스텔"],
                "투룸":    ["빌라", "오피스텔"],
                "쓰리룸":  ["빌라"],
                "오피스텔": ["오피스텔"],
                "아파트":  ["아파트"],   # 도시형생활주택·주거형 오피스텔은 사전에 '오피스텔'로 재분류됨
                "빌라":    ["빌라"],
            }
            if prop.get("type", "") not in TYPE_MAP.get(cond_prop, [cond_prop]):
                continue

        # 가격
        if deal_type == "월세":
            if condition.get("max_deposit") and deposit > condition["max_deposit"]: continue
            if condition.get("max_monthly") and monthly > condition["max_monthly"]: continue
        elif deal_type == "전세":
            max_d = condition.get("max_deposit") or condition.get("max_price")
            if max_d and deposit > max_d: continue
        elif deal_type == "매매":
            max_p = condition.get("max_price") or condition.get("max_deposit")
            if max_p and deposit > max_p: continue

        if condition.get("min_area")           and prop.get("area_m2", 0)        < condition["min_area"]:           continue
        if condition.get("min_households")     and prop.get("households", 0)     < condition["min_households"]:     continue
        if condition.get("parking_required")   and not prop.get("parking"):                                         continue
        if condition.get("building_structure") and prop.get("building_structure") != condition["building_structure"]: continue
        if condition.get("max_subway_minutes") and prop.get("subway_minutes", 99) > condition["max_subway_minutes"]: continue
        if condition.get("min_rooms")          and prop.get("rooms", 0)           < condition["min_rooms"]:          continue
        if condition.get("min_bathrooms")      and prop.get("bathrooms", 0)       < condition["min_bathrooms"]:      continue
        if condition.get("direction")          and condition["direction"] not in (prop.get("direction") or ""):      continue

        if condition.get("preferred_floor"):
            band = _floor_band(prop.get("floor", 0), prop.get("total_floors", 0))
            if band and band != condition["preferred_floor"]: continue

        if condition.get("max_building_age") and prop.get("built_year"):
            if (current_year - prop["built_year"]) > condition["max_building_age"]: continue

        # ── 점수 계산 ─────────────────────────────────────────────────────────
        score = 30  # 가격 통과 기본점

        min_area = condition.get("min_area")
        score += 20 if (not min_area or prop.get("area_m2", 0) >= min_area) else 0

        if prop.get("subway_minutes", 99) <= 7: score += 15
        if prop.get("parking"):                 score += 5

        if condition.get("min_households")     and prop.get("households", 0) >= condition["min_households"]:      score += 5
        if condition.get("building_structure") and prop.get("building_structure") == condition["building_structure"]: score += 5
        if condition.get("direction")          and condition["direction"] in (prop.get("direction") or ""):        score += 5
        if condition.get("max_building_age")   and prop.get("built_year"):
            if (current_year - prop["built_year"]) <= condition["max_building_age"]: score += 5

        score += len(prop.get("features", [])) * 3

        # 생활권 보너스
        if has_ls:
            ls_score = prop.get("lifestyle_score", 0)
            if isinstance(ls_score, (int, float)) and ls_score > 0:
                bonus = int(ls_score * 0.3)
                score += bonus

        prop["score"] = score
        passed.append(prop)

    passed.sort(key=lambda x: x["score"], reverse=True)
    return passed[:5]