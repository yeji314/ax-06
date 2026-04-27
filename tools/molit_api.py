"""
국토교통부 부동산 실거래가 공공 API 클라이언트
발급: https://www.data.go.kr → "국토교통부 아파트 전월세 자료" 검색 → 활용신청

사용 API 목록:
  전월세: RTMSDataSvcAptRent (아파트), RTMSDataSvcRHRent (연립·다세대/빌라)
  매매:   RTMSDataSvcAptTrade (아파트), RTMSDataSvcRHTrade (연립·다세대/빌라)
"""

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

import math
import requests

# ── 법정동 코드표 (서울 전 구 + 경기 주요 시) ────────────────────────────────
LAWD_CD_MAP: dict[str, str] = {
    # 서울
    "종로구": "11110", "중구": "11140",   "용산구": "11170",
    "성동구": "11200", "광진구": "11215", "동대문구": "11230",
    "중랑구": "11260", "성북구": "11290", "강북구": "11305",
    "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470",
    "강서구": "11500", "구로구": "11530", "금천구": "11545",
    "영등포구": "11560", "동작구": "11590", "관악구": "11620",
    "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740",
    # 경기
    "수원시": "41110", "성남시": "41130", "고양시": "41280",
    "용인시": "41460", "부천시": "41190", "안산시": "41270",
    "안양시": "41170", "남양주시": "41360", "화성시": "41590",
    "분당구": "41135",
}

# 랜드마크/지하철역/동 이름 → 인접 구 (LAWD_CD_MAP 매칭 실패 시 폴백)
LANDMARK_TO_GU: dict[str, str] = {
    # 중구·용산
    "서울역": "중구", "시청역": "중구", "을지로": "중구",
    "명동": "중구", "동대문": "중구", "충무로": "중구",
    "이태원": "용산구", "한남": "용산구", "한강진": "용산구",
    "용산역": "용산구", "신용산": "용산구", "후암": "용산구",
    # 강남권
    "강남역": "강남구", "역삼": "강남구", "삼성": "강남구",
    "선릉": "강남구", "도곡": "강남구", "대치": "강남구",
    "신사": "강남구", "압구정": "강남구", "청담": "강남구",
    "교대": "서초구", "고속터미널": "서초구", "방배": "서초구",
    "양재": "서초구", "잠원": "서초구",
    "잠실": "송파구", "석촌": "송파구", "문정": "송파구",
    "가락": "송파구", "위례": "송파구",
    # 마포·서대문·은평
    "홍대": "마포구", "홍대입구": "마포구", "합정": "마포구",
    "공덕": "마포구", "상수": "마포구", "망원": "마포구",
    "신촌": "서대문구", "이대": "서대문구", "충정로": "서대문구",
    "연신내": "은평구", "녹번": "은평구",
    # 성동·광진
    "성수": "성동구", "왕십리": "성동구", "행당": "성동구",
    "건대": "광진구", "구의": "광진구", "자양": "광진구",
    # 종로
    "광화문": "종로구", "종각": "종로구", "안국": "종로구",
    "혜화": "종로구", "경복궁": "종로구",
    # 영등포·동작·관악
    "여의도": "영등포구", "당산": "영등포구", "문래": "영등포구",
    "노량진": "동작구", "사당": "동작구", "흑석": "동작구",
    "신림": "관악구", "봉천": "관악구",
}

BASE = "http://apis.data.go.kr/1613000"

# 엔드포인트
EP = {
    "apt_rent":   f"{BASE}/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
    "rh_rent":    f"{BASE}/RTMSDataSvcRHRent/getRTMSDataSvcRHRent",
    "apt_trade":  f"{BASE}/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    "rh_trade":   f"{BASE}/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
}


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def get_lawd_cd(region_text: str) -> Optional[str]:
    """자유형 지역 텍스트 → 법정동 코드 5자리. 매칭 없으면 None."""
    # 1차: 구/시 직접 매칭
    for name, code in LAWD_CD_MAP.items():
        if name in region_text:
            return code

    # 2차: 랜드마크/역명 → 인접 구로 폴백
    for landmark, gu in LANDMARK_TO_GU.items():
        if landmark in region_text:
            code = LAWD_CD_MAP.get(gu)
            if code:
                print(f"[MOLIT] 랜드마크 매핑: '{landmark}' → '{gu}' ({code})")
                return code
    return None


def _recent_months(n: int = 3) -> list[str]:
    """최근 n개월 YYYYMM 리스트 (최신순)"""
    result, d = [], datetime.now()
    for _ in range(n):
        result.append(d.strftime("%Y%m"))
        d = d.replace(day=1) - timedelta(days=1)
    return result


def _g(item: ET.Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def _to_int(s: str) -> int:
    s = s.replace(",", "").replace(" ", "")
    return int(s) if s.lstrip("-").isdigit() else 0


def _to_float(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


# ── XML 파싱 ──────────────────────────────────────────────────────────────────

def _parse_rent_item(item: ET.Element, btype: str) -> Optional[dict]:
    """
    전월세 item → 매물 스키마
    ※ 2025년 이후 API 응답 태그가 영문으로 변경됨
      deposit / monthlyRent / excluUseAr / floor / buildYear / umdNm / aptNm
    """
    deposit_raw = _g(item, "deposit")
    monthly_raw = _g(item, "monthlyRent")
    if not deposit_raw:
        return None

    deposit   = _to_int(deposit_raw)
    monthly   = _to_int(monthly_raw)
    deal_type = "전세" if monthly == 0 else "월세"

    name  = _g(item, "aptNm") or _g(item, "mhouseNm") or ""
    dong  = _g(item, "umdNm")
    year  = _g(item, "dealYear")
    month = _g(item, "dealMonth")

    return {
        "id":                 "",
        "title":              f"{name} {dong}".strip() or dong,
        "region":             dong,
        "district":           dong,
        "type":               btype,
        "deal_type":          deal_type,
        "price":              {"deposit": deposit, "monthly": monthly},
        "area_m2":            _to_float(_g(item, "excluUseAr")),
        "floor":              max(_to_int(_g(item, "floor")), 0),
        "total_floors":       0,
        "households":         0,
        "parking":            btype == "아파트",
        "building_structure": "계단식",
        "subway":             "",
        "subway_minutes":     99,
        "rooms":              0,
        "bathrooms":          0,
        "direction":          "",
        "built_year":         _to_int(_g(item, "buildYear")),
        "features":           [],
        "neighborhood_features": [],
        "lifestyle_score":    0,
        "description":        f"{year}년 {month}월 실거래 ({btype} {deal_type})",
        "score":              0,
    }


def _parse_trade_item(item: ET.Element, btype: str) -> Optional[dict]:
    """
    매매 item → 매물 스키마
    ※ 2025년 이후 API 응답 태그가 영문으로 변경됨
      dealAmount / excluUseAr / floor / buildYear / umdNm / aptNm
    """
    price_raw = _g(item, "dealAmount")
    if not price_raw:
        return None

    name  = _g(item, "aptNm") or _g(item, "mhouseNm") or ""
    dong  = _g(item, "umdNm")
    year  = _g(item, "dealYear")
    month = _g(item, "dealMonth")

    return {
        "id":                 "",
        "title":              f"{name} {dong}".strip() or dong,
        "region":             dong,
        "district":           dong,
        "type":               btype,
        "deal_type":          "매매",
        "price":              {"deposit": _to_int(price_raw), "monthly": 0},
        "area_m2":            _to_float(_g(item, "excluUseAr")),
        "floor":              max(_to_int(_g(item, "floor")), 0),
        "total_floors":       0,
        "households":         0,
        "parking":            btype == "아파트",
        "building_structure": "계단식",
        "subway":             "",
        "subway_minutes":     99,
        "rooms":              0,
        "bathrooms":          0,
        "direction":          "",
        "built_year":         _to_int(_g(item, "buildYear")),
        "features":           [],
        "neighborhood_features": [],
        "lifestyle_score":    0,
        "description":        f"{year}년 {month}월 실거래 ({btype} 매매)",
        "score":              0,
    }


# ── API 호출 ──────────────────────────────────────────────────────────────────

def _fetch_page(
    endpoint: str, lawd_cd: str, deal_ymd: str,
    page: int = 1, rows: int = 1000,
) -> tuple[list[ET.Element], int]:
    """
    단일 페이지 API 호출.
    Returns: (item 리스트, totalCount)
    """
    from urllib.parse import unquote

    api_key = os.getenv("MOLIT_API_KEY", "")
    if not api_key:
        raise EnvironmentError("MOLIT_API_KEY가 .env에 설정되지 않았습니다.")

    params = {
        "serviceKey": unquote(api_key),
        "LAWD_CD":    lawd_cd,
        "DEAL_YMD":   deal_ymd,
        "numOfRows":  rows,
        "pageNo":     page,
    }
    try:
        resp = requests.get(endpoint, params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        code = root.findtext(".//resultCode") or ""
        if code and code not in ("00", "000"):
            msg = root.findtext(".//resultMsg") or ""
            print(f"[MOLIT] API 오류 코드={code} msg={msg} ({deal_ymd})")
            return [], 0

        total = int(root.findtext(".//totalCount") or "0")
        items = list(root.iter("item"))
        return items, total

    except requests.Timeout:
        print(f"[MOLIT] 타임아웃 ({deal_ymd} p{page})")
        return [], 0
    except ET.ParseError as e:
        print(f"[MOLIT] XML 파싱 오류: {e}")
        return [], 0
    except Exception as e:
        print(f"[MOLIT] 호출 실패: {type(e).__name__}: {e}")
        return [], 0


def _fetch(
    endpoint: str, lawd_cd: str, deal_ymd: str,
    rows: int = 1000, max_pages: int = 5,
) -> list[ET.Element]:
    """
    페이지네이션으로 전체 데이터 수집.

    Args:
        rows:      페이지당 건수 (기본 1000, API 최대값)
        max_pages: 최대 페이지 수 (무한 루프 방지, 기본 5 → 최대 5,000건)

    Returns:
        item Element 전체 리스트
    """
    # 1페이지 조회 → totalCount 확인
    first_items, total = _fetch_page(endpoint, lawd_cd, deal_ymd, page=1, rows=rows)
    if not first_items:
        return []

    total_pages = min(math.ceil(total / rows), max_pages)
    all_items   = list(first_items)

    if total_pages > 1:
        print(f"[MOLIT] 총 {total}건 → {total_pages}페이지 수집")
        for page in range(2, total_pages + 1):
            items, _ = _fetch_page(endpoint, lawd_cd, deal_ymd, page=page, rows=rows)
            all_items.extend(items)

    return all_items


# ── 메인 함수 ─────────────────────────────────────────────────────────────────

def search_real_properties(condition: dict) -> list[dict]:
    """
    조건에서 지역·거래유형을 추출해 실거래가 API를 호출합니다.
    최근 3개월 데이터를 합산해 반환합니다.

    Args:
        condition: UserCondition dict

    Returns:
        매물 dict 리스트 (filter_and_score_raw 입력 형식)
        지역코드 없거나 API 오류 시 []
    """
    region    = condition.get("region", "")
    deal_type = condition.get("deal_type", "")

    lawd_cd = get_lawd_cd(region)
    if not lawd_cd:
        print(f"[MOLIT] 지원하지 않는 지역: '{region}'")
        return []

    months = _recent_months(3)

    # 거래유형별 호출 대상 결정
    if deal_type in ("월세", "전세"):
        targets = [
            ("아파트", _parse_rent_item, EP["apt_rent"]),
            ("빌라",   _parse_rent_item, EP["rh_rent"]),
        ]
    elif deal_type == "매매":
        targets = [
            ("아파트", _parse_trade_item, EP["apt_trade"]),
            ("빌라",   _parse_trade_item, EP["rh_trade"]),
        ]
    else:
        targets = [
            ("아파트", _parse_rent_item,  EP["apt_rent"]),
            ("빌라",   _parse_rent_item,  EP["rh_rent"]),
            ("아파트", _parse_trade_item, EP["apt_trade"]),
            ("빌라",   _parse_trade_item, EP["rh_trade"]),
        ]

    results, idx = [], 1

    for btype, parser, endpoint in targets:
        for ym in months:
            items = _fetch(endpoint, lawd_cd, ym)
            print(f"[MOLIT] {btype} {deal_type or '전체'} {ym} → {len(items)}건")

            for item in items:
                parsed = parser(item, btype)
                if parsed:
                    parsed["id"] = f"M{idx:04d}"
                    # region에 구 이름 포함 → verify의 region 매칭 정상 작동
                    parsed["region"] = f"{region} {parsed.get('district', '')}".strip()
                    idx += 1
                    results.append(parsed)

    print(f"[MOLIT] 총 {len(results)}건 (지역: {region}, 최근 3개월)")
    return results