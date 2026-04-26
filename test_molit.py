"""
국토부 API 진단 스크립트
실행: python test_molit.py
"""

import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("MOLIT_API_KEY", "")
BASE    = "http://apis.data.go.kr/1613000"

# ── 테스트 대상 ────────────────────────────────────────────────────────────────
TEST_LAWD_CD  = "11440"   # 마포구
TEST_DEAL_YMD = (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%Y%m")  # 지난달
TEST_ENDPOINT = f"{BASE}/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"


def check_env():
    print("=" * 60)
    print("[1] 환경변수 확인")
    print("=" * 60)

    if not API_KEY:
        print("❌ MOLIT_API_KEY 미설정\n   → .env 파일에 MOLIT_API_KEY=발급받은키 추가 필요")
        return False

    print(f"✅ MOLIT_API_KEY 설정됨 (앞 10자리: {API_KEY[:10]}...)")
    print(f"   키 길이: {len(API_KEY)}자")
    print(f"   인코딩 여부: {'%' in API_KEY}")
    return True


def check_network():
    print("\n" + "=" * 60)
    print("[2] 네트워크 연결 확인")
    print("=" * 60)

    try:
        resp = requests.get("http://apis.data.go.kr", timeout=5)
        print(f"✅ data.go.kr 연결 성공 (HTTP {resp.status_code})")
        return True
    except Exception as e:
        print(f"❌ data.go.kr 연결 실패: {e}")
        return False


def check_api_call():
    print("\n" + "=" * 60)
    print("[3] API 실제 호출 테스트")
    print(f"    대상: 마포구 아파트 전월세 {TEST_DEAL_YMD}")
    print("=" * 60)

    # 방법 1: URL에 직접 키 삽입 (현재 코드 방식)
    url_direct = (
        f"{TEST_ENDPOINT}"
        f"?serviceKey={API_KEY}"
        f"&LAWD_CD={TEST_LAWD_CD}"
        f"&DEAL_YMD={TEST_DEAL_YMD}"
        f"&numOfRows=5&pageNo=1"
    )

    # 방법 2: params 딕셔너리 사용 (키 자동 인코딩)
    decoded_key = unquote(API_KEY)  # 이미 인코딩된 키라면 디코딩 후 params에 전달
    params = {
        "serviceKey": decoded_key,
        "LAWD_CD":    TEST_LAWD_CD,
        "DEAL_YMD":   TEST_DEAL_YMD,
        "numOfRows":  5,
        "pageNo":     1,
    }

    success = False

    for method, kwargs in [
        ("직접 URL", {"url": url_direct}),
        ("params 딕셔너리", {"url": TEST_ENDPOINT, "params": params}),
    ]:
        print(f"\n  시도: {method}")
        try:
            if "params" in kwargs:
                resp = requests.get(kwargs["url"], params=kwargs["params"], timeout=10)
            else:
                resp = requests.get(kwargs["url"], timeout=10)

            print(f"  HTTP 상태: {resp.status_code}")
            print(f"  응답 앞 200자:\n  {resp.text[:200]}")

            root = ET.fromstring(resp.text)
            result_code = root.findtext(".//resultCode") or ""
            result_msg  = root.findtext(".//resultMsg") or ""
            item_count  = len(list(root.iter("item")))

            print(f"\n  resultCode : {result_code}")
            print(f"  resultMsg  : {result_msg}")
            print(f"  item 개수  : {item_count}")

            if result_code in ("00", "000") and item_count > 0:
                print(f"  ✅ 성공! {method} 방식으로 데이터 {item_count}건 수신")
                # 첫 번째 item의 필드 출력
                first = list(root.iter("item"))[0]
                print("\n  첫 번째 item 필드:")
                for child in first:
                    print(f"    <{child.tag}>: {child.text}")
                success = True
                break
            elif result_code == "22":
                print("  ❌ API 호출 제한 초과 (일일 한도)")
            elif result_code == "30":
                print("  ❌ 등록되지 않은 서비스키")
            elif result_code == "31":
                print("  ❌ 활용기간 만료된 서비스키")
            else:
                print(f"  ⚠️  코드 {result_code}: {result_msg}")

        except requests.Timeout:
            print("  ❌ 타임아웃")
        except ET.ParseError as e:
            print(f"  ❌ XML 파싱 오류: {e}")
            print(f"  응답 내용: {resp.text[:300]}")
        except Exception as e:
            print(f"  ❌ 오류: {e}")

    return success


def check_filter():
    print("\n" + "=" * 60)
    print("[4] 파싱 조건 시뮬레이션")
    print("    입력: '마포구 전세 20억이하 20평대 아파트'")
    print("=" * 60)

    # GPT 파싱 결과 시뮬레이션
    condition = {
        "region":       "마포구",
        "deal_type":    "전세",
        "max_deposit":  200000,   # 20억 = 200,000만원
        "property_type": "아파트",
        "min_area":     66.0,     # 20평 ≈ 66m² (1평=3.3058m²)
    }
    print(f"\n  파싱 예상 결과: {condition}")
    print("\n  ⚠️  주의: '20평대' → min_area를 m²로 변환하지 않으면 20m²로 파싱될 수 있음")
    print("     1평 = 3.3058m² → 20평 = 66.1m²")
    print("     파싱 프롬프트에 평→m² 변환 규칙이 필요함")


if __name__ == "__main__":
    print("\n🔍 국토부 API 진단 시작\n")

    ok1 = check_env()
    ok2 = check_network() if ok1 else False
    ok3 = check_api_call() if ok2 else False
    check_filter()

    print("\n" + "=" * 60)
    print("[결과 요약]")
    print("=" * 60)
    print(f"  환경변수 : {'✅' if ok1 else '❌'}")
    print(f"  네트워크 : {'✅' if ok2 else '❌'}")
    print(f"  API 호출 : {'✅' if ok3 else '❌'}")

    if not ok3:
        print("\n[권장 조치]")
        print("  1. data.go.kr에서 인코딩된 키(Encoding) 복사 후 .env에 입력")
        print("  2. API 활용신청 승인 여부 확인")
        print("  3. 일일 호출 한도(1000건) 초과 여부 확인")