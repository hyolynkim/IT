"""
서울교통공사_빠른하차정보 API 연동 — 엘리베이터 인접 하차칸 안내
=================================================================

교통약자(임산부, 노약자, 장애인 등)가 지하철 하차 시 엘리베이터와
가장 가까운 열차 칸/출입문으로 이동할 수 있도록 안내하는 코드입니다.

응답 특징
--------
- 역 하나당 여러 설비(엘리베이터/에스컬레이터/계단) 정보가 방향별로 옵니다.
  → plfmCmgFac 값이 "엘리베이터"인 항목만 골라서 사용합니다.
- 같은 역이라도 상행/하행(또는 방면)에 따라 가까운 칸-문 번호가 다를 수 있습니다.
- qckgffVhclDoorNo 는 "칸-문" 형식의 문자열입니다 (예: "4-4" = 4번 칸 4번 문).
- 전체 편성 칸 수는 API가 제공하지 않아, 노선별 일반적인 칸 수를
  참고값으로만 표시합니다 (실제와 다를 수 있음을 안내 문구에 명시).

사전 준비
--------
1. https://www.data.go.kr 에서 "서울교통공사_빠른하차정보" 활용신청 (자동승인, 무료)
2. 발급받은 서비스키를 아래 SERVICE_KEY 값에 붙여넣기 (한 번만 하면 됨)

설치
----
    pip install requests

실행 예시
--------
    python subway_elevator_guide.py

    실행하면 아래처럼 물어봅니다:
        호선을 입력하세요 (예: 1호선): 1호선
        역 이름을 입력하세요 (예: 서울역): 서울역
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

import requests

# =============================================================================
# 설정
# =============================================================================

# 로컬에서 CLI로 직접 실행할 땐 아래 큰따옴표 안에 키를 붙여넣어도 되고,
# 서버(app.py)로 실행할 땐 환경변수 SUBWAY_API_KEY를 우선 사용합니다.

SERVICE_KEY = os.environ.get("SUBWAY_API_KEY") or "여기에_발급받은_서비스키를_입력하세요"

ENDPOINT = "https://apis.data.go.kr/B553766/inout/getFstExit"

REQUEST_TIMEOUT = 15  # seconds (공공데이터포털 서버가 느릴 때가 있어 여유있게 설정)
MAX_RETRIES = 2        # 타임아웃/일시적 오류 시 재시도 횟수

# 이동 설비 우선순위: 엘리베이터가 없으면 에스컬레이터라도 안내합니다.
FACILITY_PRIORITY = ["엘리베이터", "에스컬레이터"]


# =============================================================================
# 데이터 모델
# =============================================================================

@dataclass
class DirectionInfo:
    """방향(상행/하행)별 엘리베이터(또는 대체 설비) 인접 하차 정보"""

    direction: str        # 상행/하행
    destination: str       # 그 방향으로 갈 때의 다음 행선지 (예: "남영")
    car: int                # 칸 번호
    door: int               # 문 번호
    facility: str           # 설비 종류 (엘리베이터/에스컬레이터)
    position_desc: str = ""  # API가 주는 사람이 읽기 쉬운 위치 설명


@dataclass
class QuickGetOffInfo:
    """역 하나에 대한 엘리베이터 인접 하차칸 안내 정보 (방향별로 여러 개 가능)"""

    line: str
    station: str
    directions: list[DirectionInfo]
    station_found: bool = True  # API에 해당 역 자체가 조회됐는지 여부

    def guide_message(self) -> str:
        if not self.directions:
            return f"🚇 {self.line} {self.station}에는 안내 가능한 이동 설비 정보가 없어요."

        lines = [f"🚇 {self.line} {self.station}으로 가시는군요.", ""]

        for i, d in enumerate(self.directions, start=1):
            lines.append(f"▶ {d.destination} 방면으로 가신다면 ({d.direction})")
            lines.append(f"   {d.car}-{d.door} 문 근처에 {d.facility}가 있어요.")
            if d.position_desc:
                lines.append(f"   💡 {d.position_desc}")
            if i != len(self.directions):
                lines.append("")

        return "\n".join(lines)


# =============================================================================
# API 호출
# =============================================================================

def fetch_quick_get_off_info(
    line: str,
    station: str,
    service_key: str = SERVICE_KEY,
) -> Optional[QuickGetOffInfo]:
    """
    서울교통공사 빠른하차정보 API를 호출해 엘리베이터(또는 대체 설비)
    인접 하차칸 정보를 방향별로 가져옵니다. 결과가 여러 페이지에 걸쳐
    있으면 자동으로 모두 모아옵니다.

    "성신여대입구(돈암)역"처럼 괄호 부기명이 있는 역은, "성신여대입구역"으로
    검색하면 포함(contains) 검색에 걸리지 않을 수 있어 자동으로
    이름을 조금씩 줄여가며 재시도합니다.
    """
    if not service_key:
        print("[안내] 서비스키가 아직 입력되지 않았어요. "
              "코드 상단의 SERVICE_KEY 값을 발급받은 키로 바꿔주세요.",
              file=sys.stderr)
        return None

    for candidate in _station_search_candidates(station):
        items = _fetch_all_items(line, candidate, service_key)
        if items is None:
            return None  # 오류 메시지는 이미 출력됨
        if items:
            return _parse_items(items, line, station)

    # 모든 후보로도 결과가 없으면 '역을 찾을 수 없음'으로 처리
    return QuickGetOffInfo(line=line, station=station, directions=[], station_found=False)


def _station_search_candidates(station: str) -> list[str]:
    """
    입력한 역명으로 검색이 안 될 경우를 대비한 대체 검색어 목록을 만듭니다.
    예: "성신여대입구역" → ["성신여대입구역", "성신여대입구"]
        "총신대입구(이수)역" → ["총신대입구(이수)역", "총신대입구(이수)", "총신대입구"]
    """
    candidates = [station]

    core = station[:-1] if station.endswith("역") else station
    if core not in candidates:
        candidates.append(core)

    if "(" in core:
        before_paren = core.split("(")[0]
        if before_paren and before_paren not in candidates:
            candidates.append(before_paren)

    return candidates


def _fetch_all_items(line: str, station: str, service_key: str) -> Optional[list[dict]]:
    """주어진 검색어로 모든 페이지를 순회해 item을 모아 반환합니다. 실패 시 None."""
    all_items: list[dict] = []
    page_no = 1
    num_rows = 10  # 확인된 정상 호출과 동일하게 안전한 값 사용

    while True:
        payload = _call_page_with_retry(line, station, service_key, page_no, num_rows)
        if payload is None:
            return None  # 오류 메시지는 _call_page_with_retry 안에서 이미 출력됨

        header = payload.get("response", {}).get("header", {})
        if header.get("resultCode") not in (None, "00"):
            print(f"[경고] API 오류 응답: {header.get('resultMsg')}", file=sys.stderr)
            return None

        body = payload.get("response", {}).get("body", {})
        items = body.get("items", {}).get("item", [])
        if isinstance(items, dict):
            items = [items]
        all_items.extend(items)

        total_count = int(body.get("totalCount", len(all_items)))
        if len(all_items) >= total_count or not items:
            break
        page_no += 1

    return all_items


def _call_page_with_retry(
    line: str, station: str, service_key: str, page_no: int, num_rows: int
) -> Optional[dict]:
    """한 페이지를 호출하고, 타임아웃/일시적 서버 오류(500) 시 자동 재시도합니다."""
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 2):  # 처음 1회 + 재시도 MAX_RETRIES회
        try:
            return _call_api(line, station, service_key, page_no, num_rows)
        except requests.Timeout as exc:
            last_error = exc
            print(f"[안내] 서버 응답이 느려서 다시 시도할게요... ({attempt}/{MAX_RETRIES + 1})",
                  file=sys.stderr)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if status in (401, 500):
                last_error = exc
                print(f"[안내] 서버 일시 오류(HTTP {status}), 다시 시도할게요... "
                      f"({attempt}/{MAX_RETRIES + 1})", file=sys.stderr)
            else:
                print(f"[경고] API 서버 오류 (HTTP {status})", file=sys.stderr)
                return None
        except requests.RequestException as exc:
            print(f"[경고] 네트워크 연결에 문제가 있어요: {exc}", file=sys.stderr)
            return None

    print(f"[경고] 여러 번 시도했지만 서버가 정상 응답하지 않았어요: {last_error}", file=sys.stderr)
    print("[안내] 서비스키가 정확한지, 활용신청이 승인됐는지도 확인해보시고, "
          "잠시 후 다시 시도해주세요.", file=sys.stderr)
    return None


def _call_api(line: str, station: str, service_key: str, page_no: int, num_rows: int) -> dict:
    """실제 HTTP 요청을 보내고 JSON을 반환합니다."""
    params = {
        "serviceKey": service_key,
        "pageNo": page_no,
        "numOfRows": num_rows,
        "dataType": "JSON",
        "lineNm": line,
    }
    if station:  # 빈 문자열이면 stnNm을 아예 빼서 "해당 노선 전체 조회"로 사용
        params["stnNm"] = station
    resp = requests.get(ENDPOINT, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def list_covered_stations(line: str, service_key: str, limit: int = 20) -> Optional[list[str]]:
    """
    해당 노선에서 실제로 빠른하차정보가 등록되어 있는 역 이름 목록을 가져옵니다.
    (stnNm 없이 조회해서, 결과에 등장하는 역 이름만 중복 없이 모읍니다)
    """
    items = _fetch_all_items(line, "", service_key)
    if items is None:
        return None

    seen: list[str] = []
    for item in items:
        name = item.get("stnNm")
        if name and name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            break
    return seen


def _parse_items(items: list[dict], line: str, station: str) -> QuickGetOffInfo:
    """API에서 모아온 item 목록을 QuickGetOffInfo로 변환합니다 (방향별로 그룹화)."""
    if not items:
        return QuickGetOffInfo(line=line, station=station, directions=[], station_found=False)

    # 설비 우선순위(엘리베이터 > 에스컬레이터)에 맞는 항목만, 방향별로 하나씩 선택
    by_direction: dict[str, dict] = {}
    for item in items:
        facility = item.get("plfmCmgFac")
        if facility not in FACILITY_PRIORITY:
            continue
        direction = item.get("upbdnbSe", "")
        current = by_direction.get(direction)
        if current is None or FACILITY_PRIORITY.index(facility) < FACILITY_PRIORITY.index(current.get("plfmCmgFac")):
            by_direction[direction] = item

    directions: list[DirectionInfo] = []
    for direction, item in by_direction.items():
        car_door = item.get("qckgffVhclDoorNo", "")
        try:
            car_str, door_str = car_door.split("-")
            car, door = int(car_str), int(door_str)
        except (ValueError, AttributeError):
            continue

        directions.append(DirectionInfo(
            direction=direction,
            destination=item.get("drtnInfo", ""),
            car=car,
            door=door,
            facility=item.get("plfmCmgFac", "엘리베이터"),
            position_desc=item.get("facPstnNm") or "",
        ))

    return QuickGetOffInfo(line=line, station=station, directions=directions)


# =============================================================================
# 콘솔 출력 (교통약자를 위한 간결하고 큰 안내문)
# =============================================================================

def print_guide(info: Optional[QuickGetOffInfo], line: str, station: str) -> None:
    width = 50
    print()
    print("♿  하차 안내".center(width))
    print("=" * width)
    print()

    if info is None:
        print(f"  '{line} {station}' 정보를 가져오지 못했어요.")
        print("  서비스키와 인터넷 연결을 확인해주세요.")
    elif not info.station_found:
        print(f"  '{line} {station}' 정보를 찾을 수 없어요.")
        print("  철자가 틀렸을 수도 있지만, 서울교통공사가 아직")
        print("  이 역의 데이터를 등록하지 않았을 수도 있어요.")
    elif not info.directions:
        print(f"  '{line} {station}'에는 엘리베이터·에스컬레이터")
        print("  안내 정보가 등록되어 있지 않아요.")
        print("  역 직원에게 문의하시는 것을 권장드려요.")
    else:
        for text_line in info.guide_message().split("\n"):
            print(f"  {text_line}")

    print()
    print("=" * width)
    print()


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    print("🚇 지하철 엘리베이터 인접 하차칸 안내")
    print("(종료하려면 Ctrl+C)")
    print()

    line = input("호선을 입력하세요 (예: 1호선): ").strip()
    station = input("역 이름을 입력하세요 (예: 서울역): ").strip()

    if not station.endswith("역"):
        station += "역"

    info = fetch_quick_get_off_info(line, station)
    print_guide(info, line, station)

    if info is not None and not info.station_found:
        covered = list_covered_stations(line, SERVICE_KEY)
        if covered:
            print(f"  참고로 '{line}'에 등록된 역 중 일부는 이래요:")
            print("  " + ", ".join(covered))
            print()


if __name__ == "__main__":
    main()