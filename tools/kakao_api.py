"""
카카오 Local API 클라이언트 — geocoding(주소·키워드 → 좌표).

발급: https://developers.kakao.com → 애플리케이션 추가 → REST API 키 복사 → .env에 KAKAO_REST_API_KEY 저장.

용도:
    estimate_transit_minutes(from_text, to_text)
    → 두 위치 사이 직선 거리(Haversine) × 보정 계수로 통근 시간 추정.
    실제 대중교통 API가 아니므로 ±10분 정도의 오차가 있음.
    실제 환승 정보·러시아워 차이가 필요하면 ODsay·Naver Directions 연동 권장.
"""

import math
import os
from typing import Optional

import requests

_KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
_GEOCODE_CACHE: dict[str, Optional[tuple[float, float]]] = {}

# 서울 대중교통 평균 속도 ≈ 18 km/h (도보·환승 포함)
_AVG_SPEED_KMH = 18.0
# 직선거리 → 실 이동거리 보정
_DETOUR_FACTOR = 1.3
# 환승·역 도보 등 고정 부가 시간 (분)
_BASE_TRANSIT_OVERHEAD_MIN = 10


def _api_key() -> str:
    return os.getenv("KAKAO_REST_API_KEY", "").strip()


def is_available() -> bool:
    """KAKAO_REST_API_KEY가 설정되어 있는지."""
    return bool(_api_key())


def geocode(query: str) -> Optional[tuple[float, float]]:
    """
    카카오 Local 키워드 검색 → 첫 결과의 (lat, lon) 반환.
    실패 시 None. 결과는 세션 캐시에 저장됨.
    """
    if not query:
        return None
    cached = _GEOCODE_CACHE.get(query)
    if cached is not None:
        return cached if cached else None  # ()는 None로 처리

    key = _api_key()
    if not key:
        _GEOCODE_CACHE[query] = None
        return None

    try:
        resp = requests.get(
            _KAKAO_LOCAL_URL,
            headers={"Authorization": f"KakaoAK {key}"},
            params={"query": query, "size": 1},
            timeout=3,
        )
        if resp.status_code != 200:
            _GEOCODE_CACHE[query] = None
            return None
        docs = resp.json().get("documents", [])
        if not docs:
            _GEOCODE_CACHE[query] = None
            return None
        d = docs[0]
        coord = (float(d["y"]), float(d["x"]))  # (lat, lon)
        _GEOCODE_CACHE[query] = coord
        return coord
    except Exception as e:
        print(f"[Kakao geocode 오류] {e}")
        _GEOCODE_CACHE[query] = None
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 사이 직선거리(km)."""
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimate_transit_minutes(from_text: str, to_text: str) -> Optional[int]:
    """
    Kakao Local geocoding + Haversine 기반 대중교통 추정 시간(분).

    Args:
        from_text: 출발 텍스트 (예: '시청역', '강남역', '회사 시청')
        to_text:   도착 텍스트 (예: '서울 마포구 공덕동')

    Returns:
        추정 분 (휴리스틱: distance × detour / speed × 60 + overhead).
        Kakao 키 없거나 geocoding 실패 시 None.
    """
    if not is_available():
        return None
    p1 = geocode(from_text)
    p2 = geocode(to_text)
    if not p1 or not p2:
        return None

    km = haversine_km(p1[0], p1[1], p2[0], p2[1])
    minutes = (km * _DETOUR_FACTOR / _AVG_SPEED_KMH) * 60 + _BASE_TRANSIT_OVERHEAD_MIN
    return int(round(minutes))
