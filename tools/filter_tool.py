import re
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
    stats: dict = None,
) -> list:
    """
    매물 필터링 + 점수 계산.

    Args:
        properties: 매물 목록
        condition:  사용자 조건
        lifestyle:  생활권 조건
        stats:      탈락 사유별 카운터 (out-parameter, dict가 주어지면 채워줌)

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

    # 탈락 사유 카운터 (입력으로 받은 dict에 누적)
    if stats is not None:
        stats.setdefault("rejected_by", {})
        stats.setdefault("data_gaps", {"subway_minutes_missing": 0, "total_floors_missing": 0})
    def _reject(reason: str) -> None:
        if stats is not None:
            stats["rejected_by"][reason] = stats["rejected_by"].get(reason, 0) + 1

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
            _reject("거래유형 불일치"); continue

        # 외국인 밀집 동 제외 ('중국인 많지 않은 동네')
        if condition.get("exclude_high_foreign_density"):
            from tools.molit_api import HIGH_FOREIGN_DENSITY_DONGS
            district = prop.get("district", "") or ""
            if any(dong in district for dong in HIGH_FOREIGN_DENSITY_DONGS):
                _reject("외국인 밀집 동 제외 요청"); continue

        # 한강 인접 ('한강 근처/한강변/한강뷰')
        if condition.get("hangang_view_only"):
            from tools.molit_api import HANGANG_RIVERSIDE_DONGS
            district = prop.get("district", "") or ""
            if not any(dong in district for dong in HANGANG_RIVERSIDE_DONGS):
                _reject("한강변 동 아님"); continue

        # 통근 시간 ('회사가 시청역, 1시간 이내')
        if condition.get("max_commute_minutes") and condition.get("commute_from"):
            from tools.molit_api import estimate_commute_minutes
            est = estimate_commute_minutes(
                condition["commute_from"],
                prop.get("region", "") + " " + (prop.get("district", "") or ""),
            )
            if est is None:
                # 거점 매트릭스에 없는 구 → 통근 시간 추정 불가 → 보수적으로 거부
                if stats is not None:
                    stats["data_gaps"].setdefault("commute_unknown", 0)
                    stats["data_gaps"]["commute_unknown"] += 1
                _reject("통근 시간 추정 불가"); continue
            if est > condition["max_commute_minutes"]:
                _reject(f"통근 {est}분 > 한도 {condition['max_commute_minutes']}분"); continue

        # 매물 유형 (property_type) 필터 — 다중 값 지원 ("오피스텔,빌라" 등)
        cond_prop = condition.get("property_type")
        if cond_prop:
            TYPE_MAP = {
                "원룸":    ["빌라", "오피스텔"],
                "투룸":    ["빌라", "오피스텔"],
                "쓰리룸":  ["빌라"],
                "오피스텔": ["오피스텔"],
                "아파트":  ["아파트"],
                "빌라":    ["빌라"],
            }
            # 사용자가 OR로 여러 유형 요청 → 모두 합집합으로 허용
            cond_types = [t for t in re.split(r"[,/\s]+", cond_prop) if t]
            allowed_btypes: set[str] = set()
            for ct in cond_types:
                allowed_btypes.update(TYPE_MAP.get(ct, [ct]))
            if prop.get("type", "") not in allowed_btypes:
                _reject("방종류 불일치"); continue

        # 가격
        if deal_type == "월세":
            if condition.get("max_deposit") and deposit > condition["max_deposit"]:
                _reject("가격(보증금) 초과"); continue
            if condition.get("max_monthly") and monthly > condition["max_monthly"]:
                _reject("가격(월세) 초과"); continue
        elif deal_type == "전세":
            max_d = condition.get("max_deposit") or condition.get("max_price")
            if max_d and deposit > max_d:
                _reject("가격(전세가) 초과"); continue
        elif deal_type == "매매":
            max_p = condition.get("max_price") or condition.get("max_deposit")
            if max_p and deposit > max_p:
                _reject("가격(매매가) 초과"); continue

        if condition.get("min_area") and prop.get("area_m2", 0) < condition["min_area"]:
            _reject("최소 면적 미달"); continue
        if condition.get("min_households") and prop.get("households", 0) < condition["min_households"]:
            _reject("최소 세대수 미달"); continue
        if condition.get("parking_required") and not prop.get("parking"):
            _reject("주차 불가"); continue
        if condition.get("building_structure") and prop.get("building_structure") != condition["building_structure"]:
            _reject("건물 구조 불일치"); continue

        # 역까지 도보 — MOLIT 데이터에 정보 없음(99 sentinel)을 별도 집계
        if condition.get("max_subway_minutes"):
            sm = prop.get("subway_minutes", 99)
            if sm == 99:
                if stats is not None:
                    stats["data_gaps"]["subway_minutes_missing"] += 1
                _reject("역세권 정보 없음(데이터 한계)"); continue
            if sm > condition["max_subway_minutes"]:
                _reject("역까지 도보 시간 초과"); continue

        if condition.get("min_rooms") and prop.get("rooms", 0) < condition["min_rooms"]:
            _reject("최소 방 수 미달"); continue
        if condition.get("min_bathrooms") and prop.get("bathrooms", 0) < condition["min_bathrooms"]:
            _reject("최소 욕실 수 미달"); continue
        if condition.get("direction") and condition["direction"] not in (prop.get("direction") or ""):
            _reject("선호 방향 불일치"); continue

        if condition.get("preferred_floor"):
            band = _floor_band(prop.get("floor", 0), prop.get("total_floors", 0))
            if band and band != condition["preferred_floor"]:
                _reject("선호 층대 불일치"); continue

        # 탑층 강제 — total_floors가 없으면(=실거래 데이터 한계) 정확 매칭 불가 → 모두 탈락
        if condition.get("top_floor_only"):
            total = prop.get("total_floors", 0)
            floor = prop.get("floor", 0)
            if not total:
                if stats is not None:
                    stats["data_gaps"]["total_floors_missing"] += 1
                _reject("탑층 확정 불가(총층수 데이터 없음)"); continue
            if floor != total:
                _reject("탑층 아님"); continue

        if condition.get("max_building_age") and prop.get("built_year"):
            if (current_year - prop["built_year"]) > condition["max_building_age"]:
                _reject("연식 초과"); continue

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

        # ── 차등화 점수 (MOLIT 실데이터 활용) ─────────────────────────────────
        # 가성비 — 사용자 한도 대비 거래가가 낮을수록 가산
        max_p = (
            condition.get("max_price")
            or condition.get("max_deposit")
            or condition.get("max_monthly")
            or 0
        )
        if max_p and deposit and deal_type in ("매매", "전세"):
            ratio = deposit / max_p
            if   ratio < 0.6: score += 10  # 한도의 60% 미만 → 가성비 우수
            elif ratio < 0.8: score += 5   # 한도의 80% 미만 → 적정

        # 연식 보너스 (신축 우선)
        built = prop.get("built_year") or 0
        if built:
            age = current_year - built
            if   age <= 5:  score += 10  # 신축
            elif age <= 10: score += 5   # 준신축
            elif age >= 30: score -= 5   # 노후 감점

        # 면적 가성비 — 같은 가격 대비 큰 평수 우대
        area = prop.get("area_m2", 0) or 0
        if area >= 100:     score += 5   # 30평+
        elif area >= 84:    score += 3   # 25평+
        # 너무 작은 매물 (오피스텔 룸) 약간 감점
        if area > 0 and area < 30:       score -= 5

        # 거래 최신성 — 최근 1개월 거래는 시세 신뢰도 ↑
        deal_date = prop.get("deal_date", "") or ""
        if deal_date:
            now = datetime.now()
            this_ym = f"{now.year}-{now.month:02d}"
            if deal_date.startswith(this_ym):
                score += 3

        # 한강·학군 키워드 매칭 (lifestyle 입력했고 매물이 해당 동이면)
        ls_amens = lifestyle.get("amenities") or []
        district = prop.get("district", "") or ""
        if "한강" in ls_amens:
            from tools.molit_api import HANGANG_RIVERSIDE_DONGS
            if any(d in district for d in HANGANG_RIVERSIDE_DONGS):
                score += 10  # 한강변 매물 큰 가산

        # 생활권 보너스
        if has_ls:
            ls_score = prop.get("lifestyle_score", 0)
            if isinstance(ls_score, (int, float)) and ls_score > 0:
                bonus = int(ls_score * 0.3)
                score += bonus

        prop["score"] = max(0, score)  # 음수 방지
        passed.append(prop)

    passed.sort(key=lambda x: x["score"], reverse=True)
    return passed[:5]