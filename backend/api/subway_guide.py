"""
지하철 하차칸/환승칸 안내 통합 모듈
=================================================================

교통약자(임산부, 노약자 등)가 지하철을 이용할 때,
  1) 하차 시 엘리베이터(또는 에스컬레이터)와 가장 가까운 칸으로,
  2) 환승 시 다음 노선으로 가장 빠르게 갈아탈 수 있는 칸으로
이동할 수 있도록 안내하는 코드입니다.

사용 API / 데이터
----------------
[하차칸 안내] 서울교통공사_빠른하차정보 (data.go.kr, 무료·자동승인)
  https://www.data.go.kr/data/15143840/openapi.do
  엔드포인트: https://apis.data.go.kr/B553766/inout/getFstExit

[환승칸 안내] 서울교통공사_서울 도시철도 환승정보 (data.go.kr, CSV 다운로드)
  https://www.data.go.kr/data/15097652/openapi.do

사전 준비
--------
1. [하차칸] data.go.kr에서 "서울교통공사_빠른하차정보" 활용신청 (자동승인, 무료)
   → 이 파일과 같은 폴더의 .env 파일에 SUBWAY_API_KEY 값을 넣기
   (.env.example을 복사해서 .env로 이름을 바꾼 뒤, 키 값만 채우면 됩니다)
2. [환승칸] data.go.kr에서 "서울교통공사_서울 도시철도 환승정보" CSV 다운로드
   → 이 파일과 같은 폴더에 두기 (파일명에 "환승정보"만 포함되면 자동으로 찾음)

설치
----
    pip install requests python-dotenv

실행 예시
--------
    python subway_guide.py
"""

from __future__ import annotations

import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent

# .env 파일을 읽어서 환경변수로 등록합니다.
# 실행 위치(터미널/VS Code 실행 버튼/디버그 등)에 상관없이 항상 이 파일이 있는
# 폴더에서 .env를 찾도록, 현재 작업 폴더가 아니라 이 스크립트의 위치를 기준으로 삼습니다.
load_dotenv(dotenv_path=_THIS_DIR / ".env")


# =============================================================================
# PART 1. 하차칸 안내 — 엘리베이터/에스컬레이터 (data.go.kr 실시간 API)
# =============================================================================

# 서비스키는 코드에 직접 쓰지 않고, .env 파일의 SUBWAY_API_KEY 값을 읽습니다.
SERVICE_KEY = os.environ.get("SUBWAY_API_KEY", "")

ELEVATOR_ENDPOINT = "https://apis.data.go.kr/B553766/inout/getFstExit"

REQUEST_TIMEOUT = 15  # seconds (공공데이터포털 서버가 느릴 때가 있어 여유있게 설정)
MAX_RETRIES = 2        # 타임아웃/일시적 오류 시 재시도 횟수

# 이동 설비 우선순위: 엘리베이터가 없으면 에스컬레이터라도 안내합니다.
FACILITY_PRIORITY = ["엘리베이터", "에스컬레이터"]


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
        print("[안내] 서비스키가 아직 설정되지 않았어요. "
              ".env 파일에 SUBWAY_API_KEY 값을 넣어주세요.",
              file=sys.stderr)
        return None

    for candidate in _station_search_candidates(station):
        items = _fetch_all_elevator_items(line, candidate, service_key)
        if items is None:
            return None  # 오류 메시지는 이미 출력됨
        if items:
            return _parse_elevator_items(items, line, station)

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


def _fetch_all_elevator_items(line: str, station: str, service_key: str) -> Optional[list[dict]]:
    """주어진 검색어로 모든 페이지를 순회해 item을 모아 반환합니다. 실패 시 None."""
    all_items: list[dict] = []
    page_no = 1
    num_rows = 10  # 확인된 정상 호출과 동일하게 안전한 값 사용

    while True:
        payload = _call_elevator_page_with_retry(line, station, service_key, page_no, num_rows)
        if payload is None:
            return None  # 오류 메시지는 _call_elevator_page_with_retry 안에서 이미 출력됨

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


def _call_elevator_page_with_retry(
    line: str, station: str, service_key: str, page_no: int, num_rows: int
) -> Optional[dict]:
    """한 페이지를 호출하고, 타임아웃/일시적 서버 오류(500) 시 자동 재시도합니다."""
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 2):  # 처음 1회 + 재시도 MAX_RETRIES회
        try:
            return _call_elevator_api(line, station, service_key, page_no, num_rows)
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


def _call_elevator_api(line: str, station: str, service_key: str, page_no: int, num_rows: int) -> dict:
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
    resp = requests.get(ELEVATOR_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def list_covered_stations(line: str, service_key: str, limit: int = 20) -> Optional[list[str]]:
    """
    해당 노선에서 실제로 빠른하차정보가 등록되어 있는 역 이름 목록을 가져옵니다.
    (stnNm 없이 조회해서, 결과에 등장하는 역 이름만 중복 없이 모읍니다)
    """
    items = _fetch_all_elevator_items(line, "", service_key)
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


def _parse_elevator_items(items: list[dict], line: str, station: str) -> QuickGetOffInfo:
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
# PART 2. 환승칸 안내 — 빠른 환승 (data.go.kr CSV)
# =============================================================================

# CSV 위치를 지정하는 방법 (우선순위대로):
#   1) .env 파일에 TRANSFER_CSV_PATH=원하는/경로/파일명.csv 로 절대/상대 경로 지정
#      (상대 경로면 이 스크립트가 있는 폴더 기준으로 해석됩니다)
#   2) 아래 TRANSFER_CSV_FILENAME에 파일명만 직접 지정 (이 스크립트와 같은 폴더 기준)
#   3) 위 둘 다 비워두면, 이 스크립트가 있는 폴더와 그 하위 폴더를 통틀어
#      이름에 "환승정보"가 들어간 .csv 파일을 자동으로 찾습니다.
TRANSFER_CSV_FILENAME = ""

_NAMED_LINES = [
    "경강선", "경의선", "경춘선", "공항철도", "서해선", "수인분당선",
    "신림선", "신분당선", "우이신설경전철", "의정부경전철", "인천선",
]


def _find_transfer_csv_path() -> Optional[Path]:
    """CSV 파일 경로를 찾습니다."""
    # 1순위: .env의 TRANSFER_CSV_PATH (절대경로 또는 이 스크립트 기준 상대경로)
    env_path = os.environ.get("TRANSFER_CSV_PATH", "").strip()
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = _THIS_DIR / candidate
        return candidate if candidate.exists() else None

    # 2순위: 코드에 직접 적어둔 파일명 (이 스크립트와 같은 폴더)
    if TRANSFER_CSV_FILENAME:
        candidate = _THIS_DIR / TRANSFER_CSV_FILENAME
        return candidate if candidate.exists() else None

    # 3순위: 이 스크립트가 있는 폴더 + 모든 하위 폴더를 통틀어 자동 검색
    matches = sorted(_THIS_DIR.glob("*환승정보*.csv"))       # 같은 폴더 우선
    if not matches:
        matches = sorted(_THIS_DIR.rglob("*환승정보*.csv"))  # 하위 폴더까지 검색
    return matches[-1] if matches else None  # 여러 개면 이름순 마지막(보통 최신 날짜) 사용


@dataclass
class TransferOption:
    """환승역에서의 하차 위치 → 환승 후 승차 위치 매칭 정보"""

    from_station: str      # 환승 시작역
    from_line: str          # 지금 타고 있는(내리는) 호선
    from_direction: str     # 지금 타고 있는 열차의 방면 (예: "시청 방면")
    alight_car: int         # 하차위치(호차) — 지금 타고 있어야 할 칸
    alight_door: int        # 하차위치(문)
    to_direction: str       # 환승 후 타야 할 열차의 방면 (예: "숙대입구 방면")
    board_car: int          # 환승 승차위치(호차)
    board_door: int         # 환승 승차위치(문)
    walk_time: str          # 환승 소요시간 (MM:SS, 데이터에 없으면 빈 문자열)


def _load_transfers() -> list[TransferOption]:
    path = _find_transfer_csv_path()
    if path is None:
        print("[안내] 환승정보 CSV 파일을 찾을 수 없어요. "
              "data.go.kr에서 내려받은 CSV를 이 스크립트와 같은 폴더에 두세요.",
              file=sys.stderr)
        return []

    results: list[TransferOption] = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    results.append(TransferOption(
                        from_station=row["환승시작역"].strip(),
                        from_line=row["환승시작 호선"].strip(),
                        from_direction=row["하차 열차 방면"].strip(),
                        alight_car=int(row["하차위치(호차)"]),
                        alight_door=int(row["하차위치(문)"]),
                        to_direction=row["환승 열차 방면"].strip(),
                        board_car=int(row["환승 승차위치(호차)"]),
                        board_door=int(row["환승 승차위치(문)"]),
                        walk_time=row.get("소요시간", "").strip(),
                    ))
                except (KeyError, ValueError):
                    continue  # 값이 비어있거나 형식이 이상한 행은 건너뜀
    except OSError as e:
        print(f"[경고] CSV 파일을 읽을 수 없어요: {e}", file=sys.stderr)
        return []

    return results


TRANSFERS: list[TransferOption] = _load_transfers()


def _build_station_line_directions() -> dict[tuple[str, str], set[str]]:
    """
    (역명, 호선) → 그 호선으로 그 역에 '도착'할 때 가능한 방면 이름 집합.

    예: ('서울역', '4') -> {'회현 방면', '숙대입구 방면'}

    이 표를 이용하면, "환승 후 어느 노선으로 갈아타야 하는지"를 알고 있을 때
    (경로 데이터의 다음 지하철 구간 노선), 목적지와 무관한 다른 노선행
    옵션들을 걸러내고 실제로 타야 할 노선의 옵션만 남길 수 있습니다.
    """
    table: dict[tuple[str, str], set[str]] = {}
    for t in TRANSFERS:
        key = (t.from_station, t.from_line)
        table.setdefault(key, set()).add(t.from_direction)
    return table


_STATION_LINE_DIRECTIONS = _build_station_line_directions()


def _transfer_station_candidates(station: str) -> set[str]:
    """'서울역'처럼 역명 자체에 '역'이 포함된 경우와, '강남'처럼 안 붙는 경우를
    모두 매칭할 수 있도록 후보 이름 집합을 만듭니다."""
    candidates = {station}
    if station.endswith("역"):
        candidates.add(station[:-1])
    else:
        candidates.add(station + "역")
    return candidates


def normalize_transfer_line(lane_name: str) -> Optional[str]:
    """
    ODsay 등 경로 API에서 오는 노선명("수도권1호선", "공항철도" 등)을
    환승정보 CSV의 '환승시작 호선' 컬럼 형식("1", "공항철도" 등)으로 정규화합니다.

    1~9호선처럼 숫자로 된 노선은 숫자만 뽑고, 공항철도·경의선처럼
    이름으로 된 노선은 이름이 포함되어 있는지로 매칭합니다.
    """
    if not lane_name:
        return None

    match = re.search(r"(\d+)\s*호선", lane_name)
    if match:
        return match.group(1)

    for name in _NAMED_LINES:
        if name in lane_name:
            return name

    return None


def get_transfer_options(
    station: str,
    from_line: Optional[str] = None,
    from_direction: Optional[str] = None,
) -> list[TransferOption]:
    """
    환승역 이름을 기준으로 가능한 환승 옵션을 반환합니다.

    from_line: 지금 타고 있는 호선 (예: "1"). 지정하면 그 호선에서 내리는
               경우만 필터링합니다.
    from_direction: 지금 타고 있는 열차의 방면 (예: "시청"). 지정하면
                     방면 이름에 이 문자열이 포함된 것만 필터링합니다.
                     (원본 데이터가 "시청 방면"처럼 " 방면"이 붙어있어
                      부분 일치로 비교합니다.)
    """
    candidates = _transfer_station_candidates(station)

    results = []
    for t in TRANSFERS:
        if t.from_station not in candidates:
            continue
        if from_line is not None and t.from_line != str(from_line):
            continue
        if from_direction is not None and from_direction not in t.from_direction:
            continue
        results.append(t)

    return results


def _compute_transfer_info(station: str, from_line: Optional[str], to_line: Optional[str]) -> Optional[dict]:
    """환승역/내리는 노선/다음에 탈 노선을 받아, 그 환승 지점 하나에 대한
    가장 빠른 하차 칸-문 → 승차 칸-문 정보를 계산합니다. (여러 환승 지점에
    공통으로 쓰이는 핵심 로직만 떼어낸 헬퍼입니다.)"""
    if not station or not from_line:
        return None

    options = get_transfer_options(station, from_line=from_line)
    if not options:
        return None

    narrowed = False
    if to_line:
        candidates = _transfer_station_candidates(station)
        valid_directions: set[str] = set()
        for cand in candidates:
            valid_directions |= _STATION_LINE_DIRECTIONS.get((cand, to_line), set())

        if valid_directions:
            filtered = [o for o in options if o.to_direction in valid_directions]
            if filtered:  # 목적지 노선 정보로 좁혀졌을 때만 적용 (다 걸러지면 원래 목록 유지)
                options = filtered
                narrowed = True

    return {
        "station": station,
        "narrowed_by_destination": narrowed,  # True면 목적지 노선까지 고려해 정확히 좁혀진 결과
        "options": [
            {
                "from_direction": o.from_direction,
                "alight_car": o.alight_car,
                "alight_door": o.alight_door,
                "to_direction": o.to_direction,
                "board_car": o.board_car,
                "board_door": o.board_door,
                "walk_time": o.walk_time,
            }
            for o in options
        ],
    }


def get_transfer_tips_for_route(route: dict) -> list[dict]:
    """
    ODsay 스타일 경로(route, sub_paths를 가진 딕셔너리)를 받아,
    그 안에 있는 '지하철 → 지하철'로 바로 이어지는 환승 지점 모두에 대해
    (첫 번째 환승뿐 아니라 중간에 여러 번 환승하는 경우도 전부) 각각
    "실제로 향하는 목적지(다음 지하철 구간의 노선)"를 고려해서
    가장 빠르게 갈아탈 수 있는 하차 칸-문 정보를 계산해 리스트로 반환합니다.

    지하철 환승이 없는 경로(버스만 있거나, 지하철을 한 번만 타는 경우)면
    빈 리스트를 반환합니다. CSV에 등록 안 된 역/노선인 환승 지점은
    건너뜁니다(그 지점만 결과에서 빠지고, 다른 환승 지점은 그대로 포함).

    동작 방식(환승 지점 하나마다 동일하게 적용)
    --------
    1. 환승역에서 "내가 도착하는 노선"(from_line)으로 갈아탈 수 있는 모든 옵션을 찾고
    2. "내가 다음으로 타야 할 노선"(to_line, 그다음 지하철 구간)을 확인해서
    3. to_line으로 그 역에 도착할 때 실제로 쓰이는 방면 이름들과 겹치는
       옵션만 남깁니다 (서울역처럼 여러 노선이 겹치는 역에서, 목적지와
       무관한 다른 노선행 옵션을 걸러내기 위함입니다).

    ⚠️ 참고: to_line 방면 정보가 CSV에 없는 역이라면 좁히지 못하고, 1번에서
    찾은 옵션을 전부 반환합니다 — 이때는 각 옵션의 to_direction(방면 이름)을
    보고 맞는 것을 사용자가 고르도록 안내하세요.
    """
    if not route:
        return []

    sub_paths = route.get("sub_paths", [])
    subway_legs = [s for s in sub_paths if s.get("traffic_type") == 1]

    if len(subway_legs) < 2:
        return []  # 지하철 환승 자체가 없는 경로

    tips: list[dict] = []
    # 연속된 지하철 구간 쌍마다(= 환승 지점마다) 하나씩 계산합니다.
    for first_leg, second_leg in zip(subway_legs, subway_legs[1:]):
        station = first_leg.get("end_name")
        from_line = normalize_transfer_line(first_leg.get("lane_name", ""))
        to_line = normalize_transfer_line(second_leg.get("lane_name", ""))

        info = _compute_transfer_info(station, from_line, to_line)
        if info is not None:
            tips.append(info)

    return tips


def get_transfer_tip_for_route(route: dict) -> Optional[dict]:
    """하위 호환용: 경로의 첫 번째 환승 지점 정보만 반환합니다.
    (여러 환승 지점을 모두 원하면 get_transfer_tips_for_route를 쓰세요.)"""
    tips = get_transfer_tips_for_route(route)
    return tips[0] if tips else None


def list_transfer_stations(limit: int = 30) -> list[str]:
    """CSV에 등록된 환승역 이름 목록을 중복 없이 반환합니다 (역을 못 찾았을 때 참고용)."""
    seen: list[str] = []
    for t in TRANSFERS:
        if t.from_station not in seen:
            seen.append(t.from_station)
        if len(seen) >= limit:
            break
    return seen


def transfer_guide_message(station: str, options: list[TransferOption]) -> str:
    if not options:
        return f"'{station}'에서 등록된 환승 정보를 찾을 수 없어요."

    lines = [f"🔄 {station} 환승 안내", ""]
    for i, o in enumerate(options, start=1):
        lines.append(f"▶ {o.from_direction}으로 오셨다면 ({o.from_line}호선)")
        lines.append(f"   {o.alight_car}-{o.alight_door} 문 근처에서 내리시면")
        lines.append(f"   {o.to_direction} 열차 {o.board_car}-{o.board_door} 문 바로 앞이에요"
                      + (f" (도보 약 {o.walk_time})" if o.walk_time else ""))
        if i != len(options):
            lines.append("")

    return "\n".join(lines)


# =============================================================================
# 콘솔 출력 (하차칸 안내용)
# =============================================================================

def print_elevator_guide(info: Optional[QuickGetOffInfo], line: str, station: str) -> None:
    width = 50
    print()
    print("하차 안내".center(width))
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
# CLI — 하차칸/환승칸 안내 중 골라서 사용
# =============================================================================

def _run_elevator_cli() -> None:
    line = input("호선을 입력하세요 (예: 1호선): ").strip()
    station = input("역 이름을 입력하세요 (예: 서울역): ").strip()

    if not station.endswith("역"):
        station += "역"

    info = fetch_quick_get_off_info(line, station)
    print_elevator_guide(info, line, station)

    if info is not None and not info.station_found:
        covered = list_covered_stations(line, SERVICE_KEY)
        if covered:
            print(f"  참고로 '{line}'에 등록된 역 중 일부는 이래요:")
            print("  " + ", ".join(covered))
            print()


def _run_transfer_cli() -> None:
    print(f"(불러온 환승 데이터: {len(TRANSFERS)}건)")
    print()

    station = input("환승역 이름을 입력하세요 (예: 서울역): ").strip()
    options = get_transfer_options(station)

    print()
    print(transfer_guide_message(station, options))

    if not options:
        sample = list_transfer_stations()
        if sample:
            print()
            print("참고로 등록된 환승역 예시:", ", ".join(sample))


def main() -> None:
    print("🚇 지하철 하차칸/환승칸 안내")
    print("(종료하려면 Ctrl+C)")
    print()
    print("1) 하차칸 안내 (엘리베이터/에스컬레이터)")
    print("2) 환승칸 안내 (빠른 환승)")
    choice = input("선택하세요 (1 또는 2): ").strip()
    print()

    if choice == "2":
        _run_transfer_cli()
    else:
        _run_elevator_cli()


if __name__ == "__main__":
    main()
