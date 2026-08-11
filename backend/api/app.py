from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import sys
import requests
import json
from dataclasses import asdict
import csv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from models.route_finder import find_cat_optimal_route

try:
    from bus_congestion import get_bus_occupancy_for_route, get_gemini_general_recommendation, get_bus_congestion_trend_for_route, preload_bus_ridership  # ⬅️ 추가
    GENERAL_ROUTE_AVAILABLE = True
except ImportError as e:
    print(f"[안내] bus_congestion 모듈을 찾을 수 없어 '일반인 모드'는 비활성화됩니다: {e}")
    GENERAL_ROUTE_AVAILABLE = False

from subway_guide import (
    SERVICE_KEY,
    fetch_quick_get_off_info,
    list_covered_stations,
    get_transfer_tips_for_route,
)

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def normalize_line_name(lane_name: str):
    """ODsay 등 경로 API에서 오는 노선명("수도권1호선" 등)을
    data.go.kr 빠른하차정보 API가 이해하는 "1호선" 형식으로 정규화합니다."""
    if not lane_name:
        return None
    match = re.search(r"(\d+)\s*호선", lane_name)
    if match:
        return f"{match.group(1)}호선"
    return None


def get_elevator_tip_for_route(route):
    """경로의 마지막 지하철 구간(실제 하차역)을 찾아
    엘리베이터(또는 대체 설비) 인접 하차칸 정보를 조회합니다.
    지하철 구간이 없거나, 서비스키가 없거나, 정보를 못 찾으면 None을 반환합니다."""
    if not SERVICE_KEY or not route:
        return None

    sub_paths = route.get("sub_paths", [])
    subway_legs = [s for s in sub_paths if s.get("traffic_type") == 1]
    if not subway_legs:
        return None

    last_leg = subway_legs[-1]  # 최종 목적지에 가장 가까운 지하철 하차역 기준
    line = normalize_line_name(last_leg.get("lane_name", ""))
    station = last_leg.get("end_name")

    if not line or not station:
        return None

    if not station.endswith("역"):
        station += "역"

    try:
        info = fetch_quick_get_off_info(line, station)
    except Exception as e:
        print(f"엘리베이터 정보 조회 에러: {e}")
        return None

    if info is None or not info.station_found or not info.directions:
        return None

    directions = info.directions

    # 방면(상행/하행)이 여러 개 있으면, 실제로 사용자가 타고 온 방향(목적지 방면)에
    # 맞는 것 하나만 남깁니다. 그 구간의 "바로 이전 정거장"으로 가는 방면이면
    # (= 오던 길을 되돌아가는 방향) 지금 방향과 반대이므로 제외합니다.
    stations_list = last_leg.get("stations", [])
    if len(directions) > 1 and len(stations_list) >= 2:
        prev_station = stations_list[-2]
        narrowed = [d for d in directions if d.destination != prev_station]
        if narrowed:
            directions = narrowed

    return {
        "line": info.line,
        "station": info.station,
        "directions": [asdict(d) for d in directions],
    }


_MISSING = object()  # "아직 계산 안 함"과 "계산했는데 None(정보 없음)"을 구분하기 위한 표시자


def _walk_burden_score(route):
    """환승·도보가 적을수록(=몸에 부담이 적을수록) 작은 값. 기본(both/미지정) 정렬 기준."""
    return (
        route.get("transfer_count", 0),
        route.get("walk_time_total_min", 0),
        route.get("estimated_comfort_time_min", 0),
    )


def _elderly_score(route):
    """노약자 모드: 도보 시간을 최우선으로 최소화 (그다음 환승, 그다음 시간)."""
    return (
        route.get("walk_time_total_min", 0),
        route.get("transfer_count", 0),
        route.get("estimated_comfort_time_min", 0),
    )


def _general_optimal_score(route):
    """일반 모드와 동일한 기준(광역버스 우선 → 시간 순)으로 '가장 빠른/좋은' 경로를 고를 때 씁니다."""
    return (not route.get("has_express_bus", False), route.get("estimated_comfort_time_min", 0))


_CONGESTION_LEVEL_SCORE = {"혼잡": 2, "보통": 1, "여유": 0}


# =============================================================================
# 지하철 혼잡도 (서울교통공사 실제 데이터, subway_congestion.csv)
#
# CSV 형식: 요일구분,호선,역번호,출발역,상하구분,5시30분,6시00분,...,00시30분
# (30분 단위로 그 시간대 평균 혼잡도 %가 들어있습니다. 100% = 정원만큼 탑승)
# =============================================================================

SUBWAY_CONGESTION_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subway_congestion.csv")
_subway_congestion_cache = None


def _load_subway_congestion():
    global _subway_congestion_cache
    if _subway_congestion_cache is not None:
        return _subway_congestion_cache

    data = {}
    if os.path.exists(SUBWAY_CONGESTION_CSV_PATH):
        try:
            # 서울교통공사 원본 CSV는 CP949(EUC-KR 계열) 인코딩입니다.
            with open(SUBWAY_CONGESTION_CSV_PATH, encoding="cp949") as f:
                for row in csv.DictReader(f):
                    day_type = (row.get("요일구분") or "").strip()
                    line = (row.get("호선") or "").strip()
                    station = (row.get("출발역") or "").strip()
                    direction = (row.get("상하구분") or "").strip()
                    key = (day_type, line, station)
                    data.setdefault(key, {})[direction] = row
        except Exception as e:
            print(f"[안내] 지하철 혼잡도 CSV 로딩 실패: {e}")

    _subway_congestion_cache = data
    return data


def _weekday_to_day_type(weekday):
    """0=월요일 ... 6=일요일 (기존 코드 관례) → CSV의 요일구분 값으로 변환."""
    if weekday == 5:
        return "토요일"
    if weekday == 6:
        return "일요일"
    return "평일"


def _congestion_time_column(hour, minute):
    """hour:minute을 CSV의 30분 단위 컬럼명으로 변환합니다 (내림 처리).
    운행 정보가 없는 새벽 1~4시대는 None을 반환합니다."""
    h = hour % 24
    m = 30 if minute >= 30 else 0
    if h == 0:
        return f"00시{m:02d}분"
    if 1 <= h <= 4:
        return None  # 막차 이후 ~ 첫차 전, 데이터 없음
    return f"{h}시{m:02d}분"


def get_route_subway_congestion(route, weekday, hour, minute):
    """경로에 있는 지하철 구간들의 평균 혼잡도(%)를 반환합니다.
    각 구간은 "그 구간이 출발하는 역" 기준으로 조회하고, 상선/하선 값이
    둘 다 있으면 평균을 씁니다 (정확한 상행/하행 판별에 필요한 노선
    전체 순서 정보가 없어서, 근사치로 평균을 씁니다).
    검색 시각을 모든 구간에 그대로 쓰지 않고, 그 구간 앞에 있는 도보·지하철·
    버스 구간들의 소요시간을 누적해서 "실제로 그 구간에 도달하는 시각"을
    구해 사용합니다 — 환승 후 한참 뒤에 타는 구간이라면 검색 시각이 아니라
    그 시점의 혼잡도를 반영해요.
    데이터가 아예 없으면 None을 반환합니다."""
    data = _load_subway_congestion()
    if not data:
        return None

    day_type = _weekday_to_day_type(weekday)
    sub_paths = route.get("sub_paths", [])
    base_total_min = hour * 60 + minute

    leg_scores = []
    elapsed = 0
    for seg in sub_paths:
        if seg.get("traffic_type") == 1:
            leg_total_min = (base_total_min + elapsed) % (24 * 60)
            leg_hour, leg_minute = divmod(leg_total_min, 60)
            col = _congestion_time_column(leg_hour, leg_minute)

            line = normalize_line_name(seg.get("lane_name", ""))
            station = seg.get("start_name", "")
            if col is not None and line and station:
                by_direction = data.get((day_type, line, station))
                if by_direction:
                    values = []
                    for direction_row in by_direction.values():
                        raw = direction_row.get(col)
                        if raw not in (None, ""):
                            try:
                                values.append(float(raw))
                            except ValueError:
                                pass
                    if values:
                        leg_scores.append(sum(values) / len(values))

        elapsed += seg.get("section_time_min", 0)

    if not leg_scores:
        return None
    return sum(leg_scores) / len(leg_scores)


def _subway_congestion_label(pct):
    """서울교통공사가 실제로 쓰는 구간 기준으로 혼잡도 % → 등급 문자열 환산."""
    if pct < 34:
        return "여유"
    if pct < 80:
        return "보통"
    return "혼잡"


def get_route_subway_congestion_list(route, weekday, hour, minute):
    """경로에 있는 지하철 구간들 각각의 "지금 혼잡도(%)"를 반환합니다.
    (버스의 get_bus_occupancy_for_route와 같은 역할 — 오르내림과 무관하게,
    그 구간 시점의 현재 혼잡도를 그대로 보여줍니다.)
    검색 시각을 모든 구간에 그대로 쓰지 않고, 그 구간 앞에 있는 도보·지하철·
    버스 구간들의 소요시간을 누적해서 "실제로 그 구간에 도달하는 시각"을
    구해 사용합니다. 각 항목은 {line, station, current_pct, congestion} 형태이고,
    데이터가 없는 구간은 결과에서 빠집니다."""
    data = _load_subway_congestion()
    if not data:
        return []

    day_type = _weekday_to_day_type(weekday)
    base_total_min = hour * 60 + minute
    result = []
    elapsed = 0

    for seg in route.get("sub_paths", []):
        if seg.get("traffic_type") == 1:
            leg_total_min = (base_total_min + elapsed) % (24 * 60)
            leg_hour, leg_minute = divmod(leg_total_min, 60)
            col = _congestion_time_column(leg_hour, leg_minute)

            line = normalize_line_name(seg.get("lane_name", ""))
            station = seg.get("start_name", "")

            if col is not None and line and station:
                by_direction = data.get((day_type, line, station))
                if by_direction:
                    values = []
                    for direction_row in by_direction.values():
                        raw = direction_row.get(col)
                        if raw not in (None, ""):
                            try:
                                values.append(float(raw))
                            except ValueError:
                                pass
                    if values:
                        cur_pct = sum(values) / len(values)
                        result.append({
                            "line": line,
                            "station": station,
                            "current_pct": round(cur_pct),
                            "congestion": _subway_congestion_label(cur_pct),
                        })

        elapsed += seg.get("section_time_min", 0)

    return result


def get_route_subway_congestion_trend(route, weekday, hour, minute):
    """경로에 있는 지하철 구간들 중, 지금 혼잡도와 "다음 30분 슬롯" 혼잡도가
    다른(오르거나 내리는) 구간을 골라서 반환합니다. 각 항목은
    {line, station, current_pct, next_pct, diff_pct, direction, minutes_until_next}
    형태입니다 (diff_pct는 양수=상승, 음수=하락, direction은 "up"/"down").

    검색 시각을 모든 구간에 그대로 쓰지 않고, 그 구간 앞에 있는 도보·지하철·
    버스 구간들의 소요시간을 누적해서 "실제로 그 구간에 도달하는 시각"을 구해
    사용합니다 — 첫 지하철 구간은 검색 시각 그대로, 환승 후 타는 두 번째
    지하철 구간은 "검색 시각 + 첫 구간 이동시간(+환승 도보시간)"을 기준으로
    30분 슬롯을 비교해요. minutes_until_next도 그 구간 기준으로 계산됩니다.

    "몇 분 후면 혼잡도가 몇 % 더 오르는지/내려가는지" 안내 문구를 만드는 데 씁니다.
    혼잡도가 그대로거나 데이터가 없는 구간은 결과에서 빠집니다."""
    data = _load_subway_congestion()
    if not data:
        return []

    day_type = _weekday_to_day_type(weekday)
    base_total_min = hour * 60 + minute
    trends = []
    elapsed = 0

    for seg in route.get("sub_paths", []):
        if seg.get("traffic_type") == 1:
            leg_total_min = base_total_min + elapsed
            leg_hour = (leg_total_min // 60) % 24
            leg_minute = leg_total_min % 60

            floor_min = (leg_total_min // 30) * 30
            next_min = floor_min + 30
            minutes_until_next = next_min - leg_total_min
            next_hour = (next_min // 60) % 24
            next_minute = next_min % 60

            cur_col = _congestion_time_column(leg_hour, leg_minute)
            next_col = _congestion_time_column(next_hour, next_minute)

            line = normalize_line_name(seg.get("lane_name", ""))
            station = seg.get("start_name", "")

            if cur_col is not None and next_col is not None and line and station:
                by_direction = data.get((day_type, line, station))
                if by_direction:
                    cur_values, next_values = [], []
                    for row in by_direction.values():
                        raw_cur = row.get(cur_col)
                        raw_next = row.get(next_col)
                        if raw_cur not in (None, ""):
                            try:
                                cur_values.append(float(raw_cur))
                            except ValueError:
                                pass
                        if raw_next not in (None, ""):
                            try:
                                next_values.append(float(raw_next))
                            except ValueError:
                                pass

                    if cur_values and next_values:
                        cur_pct = sum(cur_values) / len(cur_values)
                        next_pct = sum(next_values) / len(next_values)
                        diff_pct = next_pct - cur_pct

                        if diff_pct != 0:  # 오르든 내리든, 변화가 있는 구간만 안내
                            trends.append({
                                "line": line,
                                "station": station,
                                "current_pct": round(cur_pct),
                                "next_pct": round(next_pct),
                                "diff_pct": round(diff_pct),  # 양수=상승, 음수=하락
                                "direction": "up" if diff_pct > 0 else "down",
                                "minutes_until_next": minutes_until_next,
                                # 다음 슬롯까지 30분 넘게 남았으면 추천 문구를 안 보여줍니다
                                # (지하철은 30분 단위라 사실상 거의 항상 30분 미만이에요).
                                "recommendation": (
                                    (
                                        f"{minutes_until_next}분 후에 이동하는 것을 추천합니다"
                                        if diff_pct < 0 else "지금 이동하는 것을 추천합니다"
                                    ) if minutes_until_next < 30 else None
                                ),
                            })

        elapsed += seg.get("section_time_min", 0)

    return trends


ELDERLY_WALK_TIME_MULTIPLIER = 1.3  # 노약자 실제 체감 도보 속도 반영 (약 30% 더 걸리는 것으로 가정)


def apply_elderly_walk_time(routes, multiplier=ELDERLY_WALK_TIME_MULTIPLIER):
    """노약자 모드: 도보 구간 시간을 배율만큼 늘려서 실제 체감 시간에 가깝게 조정합니다.
    각 경로의 walk_time_total_min / original_time_min / estimated_comfort_time_min도
    늘어난 만큼 같이 반영합니다. (제자리에서 route dict를 수정하고, 그대로 반환합니다.)"""
    for r in routes:
        sub_paths = r.get("sub_paths", [])
        original_walk_total = sum(
            seg.get("section_time_min", 0) for seg in sub_paths if seg.get("traffic_type") == 3
        )
        for seg in sub_paths:
            if seg.get("traffic_type") == 3:
                seg["section_time_min"] = round(seg.get("section_time_min", 0) * multiplier)
        adjusted_walk_total = sum(
            seg.get("section_time_min", 0) for seg in sub_paths if seg.get("traffic_type") == 3
        )
        extra = adjusted_walk_total - original_walk_total

        r["walk_time_total_min"] = adjusted_walk_total
        r["original_time_min"] = r.get("original_time_min", 0) + extra
        r["estimated_comfort_time_min"] = r.get("estimated_comfort_time_min", 0) + extra

    return routes


def _route_congestion_score(route, weekday=0, hour=9, minute=0):
    """경로의 혼잡도 점수 (낮을수록 덜 붐빔).
    1) 지하철 구간: subway_congestion.csv의 실제 혼잡도(%)를 0~2 스케일로 환산해서 사용.
       (34% 이하=여유(0), 34~80%=보통(1), 80%+=혼잡(2) — 서울교통공사가 실제로 쓰는 구간 기준)
    2) 지하철 데이터가 없으면, 버스 구간 혼잡도(bus_congestion.py)를 대신 사용.
       (지금은 실제 API 연동 전이라 항상 빈 값 → 결과적으로 0)
    둘 다 없으면 0(=모름, 순위에 영향 안 줌)을 반환합니다."""
    subway_pct = get_route_subway_congestion(route, weekday, hour, minute)
    if subway_pct is not None:
        if subway_pct < 34:
            return 0
        if subway_pct < 80:
            return 1
        return 2

    if not GENERAL_ROUTE_AVAILABLE:
        return 0
    occupancy = get_bus_occupancy_for_route(route.get("sub_paths", []), hour=hour, minute=minute)
    if not occupancy:
        return 0
    return sum(_CONGESTION_LEVEL_SCORE.get(o.get("congestion"), 1) for o in occupancy) / len(occupancy)


def select_accessibility_routes(routes, accessibility_type=None, weekday=0, hour=9, minute=0):
    """노약자/임산부 모드에서 화면에 보여줄 경로 5개를 고릅니다.

    accessibility_type에 따라 "AI 추천 경로" 3개를 고르는 기본 기준이 달라집니다:
      - "elderly" (노약자): 도보 시간 최소화를 최우선
      - "pregnant" (임산부): 혼잡도(버스 구간, 지금은 목업 데이터) 최소화를 최우선
      - 그 외("both" 포함) / None: 환승 → 도보 순 (기존과 동일)

    - 뒤 2개: 남은 경로 중 요금이 가장 저렴한 것 1개("최소 금액"),
      그리고 일반 모드와 같은 기준으로 가장 나은 것 1개("최적 경로").

    각 경로 dict에 category("burden"/"cost"/"optimal")와 그에 맞는
    category_label(화면에 보여줄 탭 이름)을 붙여서 반환합니다.
    이미 계산한 엘리베이터 정보는 "_elevator_info_cache"에 담아두는데,
    호출한 쪽(get_optimal_route)에서 이 캐시를 그대로 재사용하고 지워야 합니다.
    """
    if not routes:
        return []

    # route_finder가 mode="accessibility"로 이미 (환승, 도보, 시간) 순 정렬해서 주지만,
    # accessibility_type에 따라 기준이 다르면 여기서 다시 정렬합니다.
    if accessibility_type == "elderly":
        burden_sorted = sorted(routes, key=_elderly_score)
    else:
        burden_sorted = sorted(routes, key=_walk_burden_score)

    # 부담이 비슷한 상위 후보들 중, 엘리베이터 정보가 실제로 확인되는 경로를
    # 우선하기 위해 상위 몇 개만 미리 엘리베이터 조회를 해둡니다 (전체를 다 조회하면
    # 공공데이터 API를 너무 많이 호출하게 돼서, 상위 후보로 범위를 좁혔어요).
    shortlist = burden_sorted[:8]
    for r in shortlist:
        r["_elevator_info_cache"] = get_elevator_tip_for_route(r)

    if accessibility_type == "pregnant":
        # 임산부 모드는 혼잡도를 최우선으로 다시 좁힙니다.
        for r in shortlist:
            r["_congestion_score_cache"] = _route_congestion_score(r, weekday=weekday, hour=hour, minute=minute)

        def _shortlist_score(r):
            info = r.get("_elevator_info_cache")
            elevator_found = bool(info and info.get("directions"))
            return (
                r.get("_congestion_score_cache", 0),  # 혼잡도 낮은 쪽 최우선
                r.get("transfer_count", 0),
                r.get("walk_time_total_min", 0),
                0 if elevator_found else 1,
                r.get("estimated_comfort_time_min", 0),
            )
    elif accessibility_type == "elderly":
        # 노약자 모드는 도보 시간을 최우선으로 — 1차 정렬과 동일한 기준을 끝까지 유지합니다.
        def _shortlist_score(r):
            info = r.get("_elevator_info_cache")
            elevator_found = bool(info and info.get("directions"))
            return (
                r.get("walk_time_total_min", 0),  # 도보 시간 최우선
                r.get("transfer_count", 0),
                0 if elevator_found else 1,
                r.get("estimated_comfort_time_min", 0),
            )
    else:
        def _shortlist_score(r):
            info = r.get("_elevator_info_cache")
            elevator_found = bool(info and info.get("directions"))
            return (
                r.get("transfer_count", 0),
                r.get("walk_time_total_min", 0),
                0 if elevator_found else 1,  # 엘리베이터 확인된 쪽을 동점 상황에서 우선
                r.get("estimated_comfort_time_min", 0),
            )

    shortlist.sort(key=_shortlist_score)

    burden_routes = shortlist[:3]
    chosen_ids = {id(r) for r in burden_routes}

    # "최소 금액"/"최적 경로"는 전체 후보 중 진짜 1등만 인정합니다.
    # (AI 추천 경로를 제외한 나머지 중에서만 찾으면, 진짜 최저가/최적 경로가
    # 이미 AI 추천 쪽에 들어있을 때 "최소 금액"이 실제로 더 비싼 경로를
    # 가리키는 모순이 생길 수 있어서요.) 이미 AI 추천 경로 중 하나가 그
    # 기준으로도 1등이면, 같은 경로를 중복된 탭으로 또 보여주지 않고 건너뜁니다.
    true_cheapest = min(routes, key=lambda r: r.get("payment_krw", 0))
    cost_route = None if id(true_cheapest) in chosen_ids else true_cheapest
    if cost_route is not None:
        chosen_ids.add(id(cost_route))

    true_optimal = min(routes, key=_general_optimal_score)
    optimal_route = None if id(true_optimal) in chosen_ids else true_optimal

    result = []
    for i, r in enumerate(burden_routes):
        r["category"] = "burden"
        r["category_label"] = f"AI 추천 경로 {i + 1}"
        result.append(r)
    if cost_route is not None:
        cost_route["category"] = "cost"
        cost_route["category_label"] = "최소 금액"
        result.append(cost_route)
    if optimal_route is not None:
        optimal_route["category"] = "optimal"
        optimal_route["category_label"] = "최적 경로"
        result.append(optimal_route)

    # 응답에 안 실어도 되는 내부 계산용 캐시는 정리합니다 (엘리베이터 캐시는
    # 호출한 쪽에서 재사용하니 남겨두고, 혼잡도 캐시만 지웁니다).
    for r in result:
        r.pop("_congestion_score_cache", None)

    return result

def is_rush_hour(hour, minute, weekday):
    return True  # 테스트용

def get_weekday_korean(weekday):
    days = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return days[weekday] if 0 <= weekday <= 6 else "평일"

def get_rush_hour_type(hour, minute, weekday):
    total_min = hour * 60 + minute
    if 5 * 60 + 30 <= total_min <= 7 * 60 + 30:
        return "출근 러시아워"
    elif 16 * 60 + 30 <= total_min <= 19 * 60 + 30:
        return "퇴근 러시아워"
    elif 21 * 60 <= total_min <= 23 * 60:
        return "심야 러시아워"
    return "러시아워"

def get_gemini_rush_hour_recommendation(routes, start, end, hour, minute, weekday, elevator_info=None, accessibility_type=None, transfer_info=None):
    if not GEMINI_API_KEY:
        return {
            "recommended_index": 0,
            "rush_hour_tip": "API 키 설정 후 러시아워 분석이 제공됩니다.",
            "alternative": ""
        }

    try:
        weekday_str = get_weekday_korean(weekday)
        rush_type = get_rush_hour_type(hour, minute, weekday)

        routes_summary = []
        for i, r in enumerate(routes[:3]):
            sub_paths = r.get("sub_paths", [])
            path_desc = " → ".join([
                f"{s.get('start_name', '')}({s.get('lane_name', '')})"
                for s in sub_paths if s.get('traffic_type') != 3
            ]) or f"{r.get('first_start_station', '')} → {r.get('last_end_station', '')}"

            routes_summary.append({
                "index": i,
                "description": path_desc,
                "time_min": r.get("estimated_comfort_time_min"),
                "original_time_min": r.get("original_time_min"),
                "transfer_count": r.get("transfer_count", 0),
                "has_express_bus": r.get("has_express_bus", False),
                "payment_krw": r.get("payment_krw", 0)
            })

        # 엘리베이터 인접 하차칸 정보가 있으면, Gemini가 팁에 자연스럽게 녹여 넣도록 안내문 추가
        elevator_note = ""
        if elevator_info and elevator_info.get("directions"):
            d = elevator_info["directions"][0]
            elevator_note = (
                f"\n5. 추천 경로(index 0)의 하차역인 {elevator_info['station']}에서는 "
                f"{d['car']}-{d['door']} 문 근처에 {d['facility']}가 있습니다. "
                f"교통약자를 위해 이 위치 정보를 rush_hour_tip에 자연스러운 문장으로 포함해주세요."
            )

        # 환승 정보가 있으면, Gemini가 팁에 자연스럽게 녹여 넣도록 안내문 추가
        transfer_note = ""
        if transfer_info and transfer_info.get("options"):
            o = transfer_info["options"][0]
            transfer_note = (
                f"\n6. 추천 경로(index 0)는 {transfer_info['station']}에서 환승이 있습니다. "
                f"{o['alight_car']}-{o['alight_door']} 문 근처에서 내리면 "
                f"{o['to_direction']} 열차 {o['board_car']}-{o['board_door']} 문 바로 앞이라 "
                f"가장 빠르게 갈아탈 수 있습니다. 이 환승 위치 정보도 rush_hour_tip에 "
                f"자연스러운 문장으로 포함해주세요."
            )

        # 노약자/임산부 여부에 따라 Gemini가 실제로 배려한 경로를 추천하도록 안내문 추가
        accessibility_note = ""
        if accessibility_type == "pregnant":
            accessibility_note = (
                "\n7. 이 이용자는 임산부입니다. 계단·에스컬레이터보다 엘리베이터 동선을, "
                "환승 횟수가 적은 경로를 우선 고려하고, 혼잡이 심한 구간·시간대는 피하도록 추천해주세요."
            )
        elif accessibility_type == "elderly":
            accessibility_note = (
                "\n7. 이 이용자는 노약자입니다. 도보 이동 거리와 환승 횟수가 적은 경로를 우선하고, "
                "무리한 급행 환승보다는 여유 있게 갈 수 있는 동선을 추천해주세요."
            )
        elif accessibility_type == "both":
            accessibility_note = (
                "\n7. 이 이용자는 노약자 및 임산부를 위한 경로가 필요합니다. 계단·도보 이동과 환승을 "
                "최소화하고, 혼잡이 심한 구간·시간대는 피하는 방향으로 추천해주세요."
            )

        prompt = f"""
당신은 한국 수도권 대중교통 러시아워 전문가입니다.

현재 상황:
- 현재 시각: {hour}시 {minute}분
- 요일: {weekday_str}
- 시간대: {rush_type}
- 출발지: {start}
- 도착지: {end}

분석할 경로 목록:
{json.dumps(routes_summary, ensure_ascii=False, indent=2)}

위 정보를 바탕으로 다음을 분석해주세요:
1. {rush_type} 시간대의 일반적인 광역버스 혼잡 패턴 고려
2. 혼잡할 경우 1~2개 전 정거장에서 탑승하는 것이 유리한지 판단
3. 버스보다 지하철이 더 나은 대안인지 판단
4. {weekday_str} {hour}시의 실제 교통 패턴 반영{elevator_note}{transfer_note}{accessibility_note}

반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이):
{{
  "recommended_index": 0,
  "rush_hour_tip": "구체적인 러시아워 팁 (한국어, 2문장, 정거장명 포함)",
  "alternative": "대안 제안 (한국어, 없으면 빈 문자열)"
}}
"""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 500
            }
        }
        response = requests.post(url, json=payload, timeout=10)

        if response.status_code == 200:
            result = response.json()
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        else:
            print(f"Gemini API 상태코드: {response.status_code}, 응답: {response.text}")
            return {
                "recommended_index": 0,
                "rush_hour_tip": f"Gemini API 오류 ({response.status_code})",
                "alternative": ""
            }

    except Exception as e:
        print(f"Gemini 에러: {str(e)}")
        return {
            "recommended_index": 0,
            "rush_hour_tip": f"분석 중 오류: {str(e)}",
            "alternative": ""
        }

@app.route('/predict/congestion', methods=['POST'])
def predict_congestion():
    data = request.get_json()
    if not data or 'passenger_count' not in data:
        return jsonify({"error": "passenger_count 파라미터가 필요합니다."}), 400
    count = data['passenger_count']
    if count > 800:
        result = {"status": "혼잡", "code": 2}
    elif count > 300:
        result = {"status": "보통", "code": 1}
    else:
        result = {"status": "여유", "code": 0}
    return jsonify({"passenger_count": count, "prediction": result})

@app.route('/api/routes', methods=['GET'])
def get_optimal_route():
    start = request.args.get('start', '성신여대입구')
    end = request.args.get('end', '기흥역')
    hour = request.args.get('hour', default=9, type=int)
    minute = request.args.get('minute', default=0, type=int)
    weekday = request.args.get('weekday', default=0, type=int)
    mode = request.args.get('mode', default='accessibility', type=str)  # ⬅️ 추가
    accessibility_type = request.args.get('accessibility_type', default=None, type=str)  # elderly / pregnant / both

    final_result = find_cat_optimal_route(start, end, hour, mode=mode)

    if final_result.get("status") == "fail":
        return jsonify(final_result)

    routes = final_result.get("routes", [])

    # 노약자 모드: 도보 구간 시간을 실제 체감 속도에 맞게 늘립니다.
    # (정렬/선택보다 먼저 적용해야, 늘어난 도보 시간 기준으로 "AI 추천 경로"가 뽑혀요.)
    if accessibility_type == "elderly" and routes:
        routes = apply_elderly_walk_time(routes)

    # 교통약자 모드일 땐 후보 전체가 아니라, 부담 최소 3개 + 최소금액/최적 2개로
    # 화면에 보여줄 경로를 5개로 좁혀서 확정합니다.
    if mode != 'general' and routes:
        routes = select_accessibility_routes(routes, accessibility_type=accessibility_type, weekday=weekday, hour=hour, minute=minute)
        final_result["routes"] = routes

    rush_hour = is_rush_hour(hour, minute, weekday)
    rush_hour_result = None

    # 교통약자 모드(mode != 'general')일 때만 엘리베이터 인접 하차칸 정보를 조회합니다.
    # (러시아워 여부와 상관없이 항상 계산 — 엘리베이터 위치는 혼잡도와 무관한 정보라서요)
    # 화면에서 사용자가 다른 경로를 선택할 수 있으므로, 상위 경로들 각각에 대해 계산해
    # 프론트가 선택된 경로(selectedIdx)에 맞는 정보를 보여줄 수 있게 합니다.
    # transfer_info_list의 각 원소는 그 경로에 있는 "모든" 환승 지점 리스트입니다
    # (환승이 여러 번 있는 경로도 전부 반영 — 예전엔 첫 환승만 반영됐던 부분 수정).
    elevator_info_list = []
    transfer_info_list = []
    bus_occupancy_list = []
    bus_congestion_trend_list = []
    subway_congestion_list = []
    subway_congestion_trend_list = []
    if mode != 'general' and routes:
        for r in routes[:10]:
            cached = r.pop("_elevator_info_cache", _MISSING)
            elevator_info_list.append(get_elevator_tip_for_route(r) if cached is _MISSING else cached)
            transfer_info_list.append(get_transfer_tips_for_route(r))
            # 버스 구간 실시간 혼잡도 — 일반 모드에서 쓰던 함수를 교통약자 모드에도 재사용
            if GENERAL_ROUTE_AVAILABLE:
                bus_occupancy_list.append(get_bus_occupancy_for_route(r.get("sub_paths", []), hour=hour, minute=minute))
                # 버스 구간별로 "다음 정시엔 혼잡도가 오르는지/내리는지" 안내
                bus_congestion_trend_list.append(get_bus_congestion_trend_for_route(r.get("sub_paths", []), hour, minute))
            else:
                bus_occupancy_list.append([])
                bus_congestion_trend_list.append([])
            # 지하철 구간별로 "지금 평균 혼잡도"와 "다음 30분 뒤 혼잡도가 오르는지" 안내
            subway_congestion_list.append(get_route_subway_congestion_list(r, weekday, hour, minute))
            subway_congestion_trend_list.append(get_route_subway_congestion_trend(r, weekday, hour, minute))

    first_route_first_transfer = (
        transfer_info_list[0][0] if transfer_info_list and transfer_info_list[0] else None
    )

    if rush_hour and routes:
        if mode == 'general':
            if GENERAL_ROUTE_AVAILABLE:
                # ⬅️ 일반인 모드: 실시간 여석 반영
                occupancy_data = get_bus_occupancy_for_route(routes[0].get("sub_paths", []), hour=hour, minute=minute)
                rush_hour_result = get_gemini_general_recommendation(
                    routes, occupancy_data, start, end, hour, minute, weekday
                )
            else:
                rush_hour_result = {
                    "recommended_index": 0,
                    "rush_hour_tip": "일반인 모드 기능(bus_congestion)이 아직 준비되지 않았습니다.",
                    "alternative": "",
                }
        else:
            # 교통약자 모드: 기존 로직 + 엘리베이터 정보 + 환승 정보 + 노약자/임산부 여부 반영
            rush_hour_result = get_gemini_rush_hour_recommendation(
                routes, start, end, hour, minute, weekday,
                elevator_info=elevator_info_list[0] if elevator_info_list else None,
                accessibility_type=accessibility_type,
                transfer_info=first_route_first_transfer,
            )

    final_result["is_rush_hour"] = rush_hour
    final_result["rush_hour_result"] = rush_hour_result
    final_result["accessibility_type"] = accessibility_type
    # elevator_info / transfer_info: 하위 호환용 (첫 번째 경로의 첫 번째 환승 기준)
    final_result["elevator_info"] = elevator_info_list[0] if elevator_info_list else None
    final_result["transfer_info"] = first_route_first_transfer
    # elevator_info_list: 경로별 엘리베이터 안내 (routes 배열과 동일한 순서/길이)
    # transfer_info_list: 경로별 "환승 지점 리스트" (각 원소가 그 경로의 모든 환승 정보 배열)
    # bus_occupancy_list: 경로별 버스 구간 혼잡도 (bus_congestion 없으면 전부 빈 배열)
    final_result["elevator_info_list"] = elevator_info_list
    final_result["transfer_info_list"] = transfer_info_list
    final_result["bus_occupancy_list"] = bus_occupancy_list
    final_result["bus_congestion_trend_list"] = bus_congestion_trend_list
    final_result["subway_congestion_trend_list"] = subway_congestion_trend_list
    final_result["subway_congestion_list"] = subway_congestion_list

    return jsonify(final_result)


# =============================================================================
# 엘리베이터 인접 하차칸 안내 (신규 추가)
# =============================================================================

@app.route('/api/elevator/guide', methods=['GET'])
def get_elevator_guide():
    """노선/역명을 받아 엘리베이터(또는 대체 설비) 인접 하차칸 정보를 반환합니다."""
    line = request.args.get('line')
    station = request.args.get('station')

    if not line or not station:
        return jsonify({
            "status": "error",
            "message": "line과 station 파라미터가 필요합니다. 예: /api/elevator/guide?line=2호선&station=강남역"
        }), 400

    if not station.endswith("역"):
        station += "역"

    if not SERVICE_KEY:
        return jsonify({
            "status": "error",
            "reason": "no_service_key",
            "message": "서버에 서비스키가 설정되지 않았어요. .env 파일에 SUBWAY_API_KEY 값을 넣어주세요.",
        })

    info = fetch_quick_get_off_info(line, station)

    if info is None:
        return jsonify({
            "status": "error",
            "reason": "api_call_failed",
            "message": "공공데이터 API 호출에 실패했어요. 잠시 후 다시 시도해주세요.",
        })

    if not info.station_found:
        covered = list_covered_stations(line, SERVICE_KEY)
        return jsonify({
            "status": "not_found",
            "line": line,
            "station": station,
            "message": "해당 역 정보를 찾을 수 없어요. 철자를 확인하거나, "
                       "아직 데이터가 등록되지 않았을 수 있어요.",
            "covered_stations": covered or [],
        })

    if not info.directions:
        return jsonify({
            "status": "no_facility",
            "line": line,
            "station": station,
            "message": "이 역에는 엘리베이터·에스컬레이터 안내 정보가 등록되어 있지 않아요.",
        })

    return jsonify({
        "status": "ok",
        "line": info.line,
        "station": info.station,
        "directions": [asdict(d) for d in info.directions],
    })


@app.route('/api/elevator/stations', methods=['GET'])
def get_elevator_stations():
    """해당 노선에서 실제로 데이터가 등록된 역 목록을 반환합니다."""
    line = request.args.get('line')
    limit = request.args.get('limit', default=30, type=int)

    if not line:
        return jsonify({"status": "error", "message": "line 파라미터가 필요합니다."}), 400

    if not SERVICE_KEY:
        return jsonify({"status": "error", "reason": "no_service_key", "stations": []})

    covered = list_covered_stations(line, SERVICE_KEY, limit=limit)
    if covered is None:
        return jsonify({"status": "error", "reason": "api_call_failed", "stations": []})

    return jsonify({"status": "ok", "line": line, "stations": covered})


if __name__ == '__main__':
    if GENERAL_ROUTE_AVAILABLE:
        preload_bus_ridership()  # 서버 뜨기 전에 미리 로딩 (첫 검색이 느려지는 것 방지)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
