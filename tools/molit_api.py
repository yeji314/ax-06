"""
국토교통부 부동산 실거래가 공공 API 클라이언트
발급: https://www.data.go.kr → "국토교통부 아파트 전월세 자료" 검색 → 활용신청

사용 API 목록:
  전월세: RTMSDataSvcAptRent (아파트), RTMSDataSvcRHRent (연립·다세대/빌라)
  매매:   RTMSDataSvcAptTrade (아파트), RTMSDataSvcRHTrade (연립·다세대/빌라)
"""

import os
import re
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

# 인접 구 (verify 재시도 시 검색 범위를 점진 확장)
NEIGHBOR_GU: dict[str, list[str]] = {
    "종로구":   ["중구", "성북구", "서대문구"],
    "중구":     ["용산구", "종로구", "마포구"],
    "용산구":   ["중구", "마포구", "성동구"],
    "성동구":   ["광진구", "용산구", "동대문구"],
    "광진구":   ["성동구", "중랑구", "동대문구"],
    "동대문구": ["중랑구", "성북구", "성동구"],
    "중랑구":   ["광진구", "동대문구", "노원구"],
    "성북구":   ["종로구", "강북구", "동대문구"],
    "강북구":   ["성북구", "도봉구", "노원구"],
    "도봉구":   ["노원구", "강북구"],
    "노원구":   ["도봉구", "강북구", "중랑구"],
    "은평구":   ["서대문구", "마포구"],
    "서대문구": ["마포구", "은평구", "종로구"],
    "마포구":   ["용산구", "서대문구", "영등포구"],
    "양천구":   ["강서구", "영등포구", "구로구"],
    "강서구":   ["양천구"],
    "구로구":   ["영등포구", "양천구", "금천구"],
    "금천구":   ["관악구", "구로구"],
    "영등포구": ["마포구", "동작구", "양천구"],
    "동작구":   ["영등포구", "관악구", "서초구"],
    "관악구":   ["동작구", "서초구", "금천구"],
    "서초구":   ["강남구", "동작구", "관악구"],
    "강남구":   ["서초구", "송파구"],
    "송파구":   ["강남구", "강동구"],
    "강동구":   ["송파구"],
}


def get_base_gu(region_text: str) -> Optional[str]:
    """region 텍스트에서 베이스 구 이름을 추출 (인접 구 확장용)."""
    for gu in LAWD_CD_MAP:
        if gu in region_text and gu.endswith("구"):
            return gu
    for landmark, gu in LANDMARK_TO_GU.items():
        if landmark in region_text:
            return gu
    return None


# 광역 region (구 미지정) 감지용
BROAD_REGION_TOKENS = {"서울", "수도권", "경기", "전국"}


def is_broad_region(region_text: str) -> bool:
    """베이스 구를 추출할 수 없고 광역 토큰만 들어있는지."""
    if get_base_gu(region_text) is not None:
        return False
    return any(tok in region_text for tok in BROAD_REGION_TOKENS)


# 지하철 역 → 도보권 법정동 (10분 내외) — 사용자가 'OO역 근처' 라고 했을 때
# MOLIT 결과를 해당 동들로 좁히기 위한 매핑
STATION_TO_NEAR_DONGS: dict[str, list[str]] = {
    # 1호선
    "서울역":   ["봉래동", "동자동", "회현동", "남대문로"],
    "시청역":   ["정동", "태평로", "명동"],
    "종각역":   ["관철동", "종로"],
    "신용산역": ["한강로"],
    "용산역":   ["한강로"],
    "이촌역":   ["이촌동"],
    # 2호선
    "강남역":   ["역삼동", "서초동"],
    "역삼역":   ["역삼동"],
    "선릉역":   ["역삼동", "삼성동"],
    "삼성역":   ["삼성동"],
    "잠실역":   ["잠실동", "신천동"],
    "성수역":   ["성수동1가", "성수동2가"],
    "왕십리역": ["행당동", "도선동", "하왕십리동", "상왕십리동"],
    "이대역":   ["대신동", "대현동"],
    "신촌역":   ["창천동", "노고산동"],
    "홍대입구역": ["서교동", "동교동", "합정동"],
    "합정역":   ["합정동", "망원동"],
    "당산역":   ["당산동"],
    "을지로입구역": ["을지로", "명동"],
    "건대입구역": ["화양동", "자양동"],
    # 3호선
    "옥수역":   ["옥수동", "금호동", "응봉동"],
    "금호역":   ["금호동", "옥수동"],
    "압구정역": ["압구정동"],
    "신사역":   ["신사동", "압구정동"],
    "교대역":   ["서초동", "방배동"],
    "양재역":   ["양재동"],
    "도곡역":   ["도곡동"],
    "대치역":   ["대치동"],
    "안국역":   ["안국동", "관훈동"],
    # 4호선
    "동대문역": ["창신동", "종로"],
    "혜화역":   ["혜화동", "명륜동"],
    "삼각지역": ["한강로"],
    "숙대입구역": ["갈월동", "남영동"],
    # 5호선
    "공덕역":   ["공덕동", "신공덕동"],
    "여의도역": ["여의도동"],
    "광화문역": ["신문로", "당주동"],
    "충정로역": ["충정로", "합동"],
    "마포역":   ["용강동", "도화동"],
    "애오개역": ["아현동"],
    # 6호선
    "이태원역": ["이태원동"],
    "한강진역": ["한남동"],
    "녹사평역": ["용산동", "이태원동"],
    "버티고개역": ["신당동", "한남동"],
    "효창공원앞역": ["효창동"],
    "삼각지역_6": ["한강로"],
    # 7호선
    "고속터미널역": ["반포동"],
    "청담역":   ["청담동"],
    "건대입구역_7": ["화양동", "자양동"],
    # 8호선
    "송파역":   ["송파동"],
    "석촌역":   ["석촌동"],
    "문정역":   ["문정동"],
    # 9호선
    "노량진역": ["노량진동"],
    "흑석역":   ["흑석동"],
    "동작역":   ["동작동"],
    "신반포역": ["반포동"],
    # 분당선/수인분당선
    "압구정로데오역": ["청담동"],
    "한티역":   ["대치동"],
    "강남구청역": ["삼성동"],
    "선정릉역": ["삼성동"],
    "구의역":   ["구의동"],
    "강변역":   ["구의동"],
    "성수역_분당": ["성수동1가"],
    # 6호선/공항철도
    "DMC역":   ["상암동"],
    "디지털미디어시티역": ["상암동"],
}


# 등록 외국인(특히 중국인) 비중이 높은 것으로 알려진 동들 — 통계청 KOSIS 시군구별
# 외국인 등록 데이터 기준 (2024 추정). '중국인 많지 않은' 같은 부정 요청 시 제외용.
HIGH_FOREIGN_DENSITY_DONGS: set[str] = {
    "대림동",     # 영등포구·구로구 (중국 동포 밀집)
    "가리봉동",   # 구로구
    "구로동",     # 구로구 일부
    "자양동",     # 광진구 자양4동 등
    "역삼동",     # 강남구 일부 (외국계 비즈니스)
    "이태원동",   # 용산구 (다국적)
    "한남동",     # 용산구 (다국적)
}


def get_dongs_near_station(text: str) -> list[str]:
    """region 텍스트에 역 이름이 있으면 도보권 법정동 리스트 반환.
    여러 역이 들어있으면 모두 합집합으로 반환.
    """
    if not text:
        return []
    seen: list[str] = []
    for station, dongs in STATION_TO_NEAR_DONGS.items():
        # '_' 이하는 별칭 키 (중복 역명 처리용) → 매칭 시 무시
        canonical = station.split("_")[0]
        if canonical in text:
            for d in dongs:
                if d not in seen:
                    seen.append(d)
    return seen


# 지하철 노선 → 대표 구 추천 (region이 노선명일 때 사용)
SUBWAY_LINE_TO_GU: dict[str, list[str]] = {
    "1호선":   ["종로구", "중구", "용산구", "동대문구", "구로구"],
    "2호선":   ["마포구", "성동구", "강남구", "송파구", "영등포구"],
    "3호선":   ["은평구", "종로구", "강남구", "서초구"],
    "4호선":   ["중구", "용산구", "동작구", "노원구"],
    "5호선":   ["강서구", "마포구", "광진구", "송파구", "강동구"],
    "6호선":   ["은평구", "마포구", "용산구", "성북구", "동대문구"],
    "7호선":   ["강남구", "송파구", "광진구", "노원구", "도봉구"],
    "8호선":   ["송파구", "강동구"],
    "9호선":   ["강서구", "영등포구", "동작구", "서초구", "강남구"],
    "분당선":   ["강남구", "성남시"],
    "신분당선": ["강남구", "서초구", "성남시"],
    "수인분당선": ["강남구", "성남시", "수원시"],
    "경의중앙선": ["용산구", "마포구"],
    "공항철도":  ["중구", "용산구", "마포구", "강서구"],
    "우이신설선": ["성북구", "강북구"],
    "신림선":   ["영등포구", "동작구", "관악구"],
}


def infer_gus_from_subway_line(text: str, max_count: int = 3) -> list[str]:
    """텍스트에 'N호선' 패턴이 있으면 그 노선의 대표 구를 반환."""
    if not text:
        return []
    seen: list[str] = []
    for line, gus in SUBWAY_LINE_TO_GU.items():
        if line in text:
            for g in gus:
                if g not in seen:
                    seen.append(g)
    return seen[:max_count]


# 라이프스타일/키워드 → 대표 구 추천 (광역 region일 때 사용)
LIFESTYLE_KEYWORD_TO_GU: dict[str, list[str]] = {
    "학군":      ["강남구", "서초구", "양천구", "노원구", "송파구"],
    "교육":      ["강남구", "서초구", "양천구", "노원구"],
    "대치":      ["강남구"],
    "목동":      ["양천구"],
    "중계":      ["노원구"],
    "대학":      ["서대문구", "성북구", "동대문구", "관악구"],
    "한강":      ["용산구", "마포구", "영등포구", "광진구", "성동구"],
    "공원":      ["송파구", "양천구", "광진구", "노원구"],
    "카페":      ["마포구", "용산구", "성동구"],
    "카페거리":  ["마포구", "용산구", "성동구"],
    "쇼핑":      ["중구", "강남구", "송파구"],
    "번화가":    ["중구", "강남구", "마포구"],
    "조용":      ["서초구", "양천구", "송파구"],
    "자연":      ["노원구", "강북구", "도봉구"],
    "런닝":      ["용산구", "성동구", "송파구"],
    "자전거":    ["성동구", "송파구", "광진구"],
    "헬스":      ["강남구", "서초구"],
    "맛집":      ["마포구", "용산구", "강남구"],
    "역세권":    ["중구", "강남구", "마포구"],
}


def infer_gus_from_lifestyle(
    lifestyle: dict, max_count: int = 3
) -> list[str]:
    """라이프스타일 dict에서 키워드를 뽑아 대표 구 리스트로 변환."""
    if not lifestyle:
        return []

    haystack_parts = [
        str(lifestyle.get("raw_keywords") or ""),
        str(lifestyle.get("atmosphere") or ""),
        " ".join(lifestyle.get("activities") or []),
        " ".join(lifestyle.get("amenities") or []),
    ]
    haystack = " ".join(haystack_parts)

    # 키워드 등장 순으로 추천 구를 모음 (중복 제거 + 순서 보존)
    seen: list[str] = []
    for keyword, gus in LIFESTYLE_KEYWORD_TO_GU.items():
        if keyword in haystack:
            for g in gus:
                if g not in seen:
                    seen.append(g)
    return seen[:max_count]


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
    "금호": "성동구", "옥수": "성동구", "응봉": "성동구",
    "마장": "성동구", "사근": "성동구", "송정": "성동구",
    "건대": "광진구", "구의": "광진구", "자양": "광진구",
    "능동": "광진구", "중곡": "광진구", "화양": "광진구",
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


def _format_deal_date(year: str, month: str, day: str = "") -> str:
    """MOLIT의 dealYear/Month/Day → 'YYYY-MM-DD' 또는 'YYYY-MM' (day 없을 때)."""
    if not year or not month:
        return ""
    y = year.strip().zfill(4)
    m = month.strip().zfill(2)
    d = (day or "").strip().zfill(2) if day else ""
    return f"{y}-{m}-{d}" if d else f"{y}-{m}"


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
    day   = _g(item, "dealDay")
    deal_date = _format_deal_date(year, month, day)

    return {
        "id":                 "",
        "title":              f"{name} {dong}".strip() or dong,
        "region":             dong,
        "district":           dong,
        "type":               _classify_real_type(name, btype),
        "deal_type":          deal_type,
        "price":              {"deposit": deposit, "monthly": monthly},
        "area_m2":            _to_float(_g(item, "excluUseAr")),
        "floor":              max(_to_int(_g(item, "floor")), 0),
        "total_floors":       0,
        "households":         0,
        "parking":            _classify_real_type(name, btype) == "아파트",
        "building_structure": "계단식",
        "subway":             "",
        "subway_minutes":     99,
        "rooms":              0,
        "bathrooms":          0,
        "direction":          "",
        "built_year":         _to_int(_g(item, "buildYear")),
        "deal_date":          deal_date,
        "features":           [],
        "neighborhood_features": [],
        "lifestyle_score":    0,
        "description":        f"{deal_date} 실거래 ({btype} {deal_type})",
        "score":              0,
    }


# MOLIT의 '아파트 매매' 응답에는 등기상 아파트로 분류된 도시형생활주택·주거형 오피스텔·
# 다세대주택(빌라)이 섞여 들어옴. 이름의 키워드로 재분류해서 사용자가 '아파트' 요청 시
# 진짜 아파트만 추리도록 함.
# ※ '타워'·'시티'·'비젼'·'센트레빌' 같은 단어는 진짜 아파트 단지명에도 흔해서 제외
#   (예: 타워팰리스, 센텀시티, 비젼21, 동부센트레빌).
OFFICETEL_NAME_HINTS = (
    "오피스텔", "고시텔", "리빙텔",
    "이빌", "디오빌", "디오슈페리움",
    "헤리츠빌", "리시온", "리치파크",
    "스튜디오", "더 리브",
)

VILLA_NAME_HINTS = (
    "맨션",  # OO맨션은 보통 다세대주택
    "다세대", "연립", "원룸",
)

# 진짜 아파트인데 이름이 'OO빌'로 끝나는 예외 단지 (정규식 매칭에서 제외)
APT_BRAND_WHITELIST = (
    "센트레빌",  # 동부 센트레빌 — 진짜 아파트 브랜드
)

# 'OO빌' suffix 패턴 — 한글 2~4자 + '빌' 다음에 단어 경계(공백·괄호·숫자·점·끝)
_VILLA_SUFFIX_RE = re.compile(r"[가-힣]{2,4}빌(?=[\s\(\)\[\]\d.,]|$)")


def _looks_like_villa(name: str) -> bool:
    """이름의 'OO빌' suffix 패턴으로 빌라 판정 (브랜드 키워드 의존도 줄임)."""
    if not name:
        return False
    if any(brand in name for brand in APT_BRAND_WHITELIST):
        return False
    return bool(_VILLA_SUFFIX_RE.search(name))


def _classify_real_type(name: str, default_btype: str) -> str:
    """매물 이름·패턴으로 type 재분류.
    우선순위: 오피스텔 명시 > 빌라 패턴/명시 > 기본(엔드포인트 라벨).
    """
    if not name:
        return default_btype
    if any(h in name for h in OFFICETEL_NAME_HINTS):
        return "오피스텔"
    if _looks_like_villa(name) or any(h in name for h in VILLA_NAME_HINTS):
        return "빌라"
    return default_btype


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
    day   = _g(item, "dealDay")
    deal_date = _format_deal_date(year, month, day)

    return {
        "id":                 "",
        "title":              f"{name} {dong}".strip() or dong,
        "region":             dong,
        "district":           dong,
        "type":               _classify_real_type(name, btype),
        "deal_type":          "매매",
        "price":              {"deposit": _to_int(price_raw), "monthly": 0},
        "area_m2":            _to_float(_g(item, "excluUseAr")),
        "floor":              max(_to_int(_g(item, "floor")), 0),
        "total_floors":       0,
        "households":         0,
        "parking":            _classify_real_type(name, btype) == "아파트",
        "building_structure": "계단식",
        "subway":             "",
        "subway_minutes":     99,
        "rooms":              0,
        "bathrooms":          0,
        "direction":          "",
        "built_year":         _to_int(_g(item, "buildYear")),
        "deal_date":          deal_date,
        "features":           [],
        "neighborhood_features": [],
        "lifestyle_score":    0,
        "description":        f"{deal_date} 실거래 ({btype} 매매)",
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
    region        = condition.get("region", "")
    deal_type     = condition.get("deal_type", "")
    property_type = condition.get("property_type", "")

    lawd_cd = get_lawd_cd(region)
    if not lawd_cd:
        print(f"[MOLIT] 지원하지 않는 지역: '{region}'")
        return []

    months = _recent_months(3)

    # property_type에 맞는 엔드포인트만 호출 (다중 값 "오피스텔,빌라" 등 지원)
    pt_parts = [p for p in re.split(r"[,/\s]+", property_type) if p] if property_type else []
    if not pt_parts:
        use_apt = True
        use_rh  = True
    else:
        use_apt = any(p in ("아파트", "오피스텔", "원룸", "투룸", "쓰리룸") for p in pt_parts)
        use_rh  = any(p in ("빌라", "오피스텔", "원룸", "투룸", "쓰리룸") for p in pt_parts)

    # 거래유형별 호출 대상 결정
    if deal_type in ("월세", "전세"):
        targets = []
        if use_apt: targets.append(("아파트", _parse_rent_item, EP["apt_rent"]))
        if use_rh:  targets.append(("빌라",   _parse_rent_item, EP["rh_rent"]))
    elif deal_type == "매매":
        targets = []
        if use_apt: targets.append(("아파트", _parse_trade_item, EP["apt_trade"]))
        if use_rh:  targets.append(("빌라",   _parse_trade_item, EP["rh_trade"]))
    else:
        targets = [
            ("아파트", _parse_rent_item,  EP["apt_rent"]),
            ("빌라",   _parse_rent_item,  EP["rh_rent"]),
            ("아파트", _parse_trade_item, EP["apt_trade"]),
            ("빌라",   _parse_trade_item, EP["rh_trade"]),
        ]

    results, idx = [], 1
    btype_counts: dict[str, int] = {}

    for btype, parser, endpoint in targets:
        for ym in months:
            items = _fetch(endpoint, lawd_cd, ym)
            for item in items:
                parsed = parser(item, btype)
                if parsed:
                    parsed["id"] = f"M{idx:04d}"
                    # region에 구 이름 포함 → verify의 region 매칭 정상 작동
                    parsed["region"] = f"{region} {parsed.get('district', '')}".strip()
                    idx += 1
                    results.append(parsed)
                    btype_counts[btype] = btype_counts.get(btype, 0) + 1

    breakdown = ", ".join(f"{k} {v}건" for k, v in btype_counts.items()) or "0건"
    print(f"[MOLIT] {region} {deal_type or '전체'} 3개월: {breakdown} = 총 {len(results)}건")
    return results


def search_real_properties_expanded(condition: dict, neighbor_count: int = 0) -> list[dict]:
    """
    region을 공백으로 분리해 여러 구를 동시 조회 + 인접 구 N개 추가 조회.

    Args:
        condition:      UserCondition dict (region에 공백 구분 다중 구 가능)
        neighbor_count: 첫 구 기준으로 추가 조회할 인접 구 수

    Returns:
        매물 dict 리스트 (id 충돌 방지 처리)
    """
    region = condition.get("region", "") or ""
    # 공백으로 분리해 각 토큰을 개별 구로 처리 (LAWD_CD 매칭되는 것만)
    tokens = [t.strip() for t in region.split() if t.strip()]
    primary_gus: list[str] = []
    for tok in tokens:
        gu = get_base_gu(tok)
        if gu and gu not in primary_gus:
            primary_gus.append(gu)

    # primary 구 추출 실패 시 기본 동작 (search_real_properties가 실패 로그 출력)
    if not primary_gus:
        return search_real_properties(condition)

    # 인접 구 보강 (첫 구 기준)
    if neighbor_count > 0:
        for n_gu in NEIGHBOR_GU.get(primary_gus[0], []):
            if n_gu not in primary_gus and len(primary_gus) - 1 < len(primary_gus) + neighbor_count:
                primary_gus.append(n_gu)
                if len(primary_gus) >= len(tokens) + neighbor_count + 1:
                    break

    if len(primary_gus) > 1:
        print(f"[MOLIT] 다중 구 동시 조회: {primary_gus}")

    seen_ids: set[str] = set()
    merged: list[dict] = []
    next_idx = 1

    for gu in primary_gus:
        results = search_real_properties({**condition, "region": gu})
        for p in results:
            new_id = f"M{next_idx:04d}"
            while new_id in seen_ids:
                next_idx += 1
                new_id = f"M{next_idx:04d}"
            p["id"] = new_id
            seen_ids.add(new_id)
            next_idx += 1
            merged.append(p)

    if len(primary_gus) > 1:
        print(f"[MOLIT] 다중 구 합산: 총 {len(merged)}건")
    return merged