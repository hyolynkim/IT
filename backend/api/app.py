from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import sys
import requests, time
import json
import csv
from dataclasses import asdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

# gunicorn이 'api.app:app'처럼 패키지 경로로 이 모듈을 import할 때는
# (python app.py로 직접 실행할 때와 달리) 이 파일이 있는 api/ 폴더 자체가
# import 경로에 안 잡혀서, 같은 폴더의 subway_guide/tago_service/bus_congestion
# 등을 못 찾는 문제가 있었음. 그래서 이 sys.path.append 이후에만 그 모듈들을 import해야 함.
API_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(API_DIR)

from tago_service import get_route_congestion
from bus_congestion import CongestionEstimator

est = CongestionEstimator()

from models.route_finder import find_cat_optimal_route

try:
    from services.general_route import (
        get_bus_occupancy_for_route,
        get_gemini_general_recommendation,
        # 교통약자 모드의 러시아워 추천(get_gemini_rush_hour_recommendation)도 같은
        # Claude 클라이언트/모델/스키마를 재사용함 — 여기서 같이 가져옴.
        _get_anthropic_client,
        _ANTHROPIC_AVAILABLE,
        CLAUDE_RUSH_HOUR_MODEL,
        _RUSH_HOUR_TIP_SCHEMA,
    )
    GENERAL_ROUTE_AVAILABLE = True
except ModuleNotFoundError as e:
    print(f"[안내] services.general_route 모듈을 찾을 수 없어 '일반인 모드'는 비활성화됩니다: {e}")
    GENERAL_ROUTE_AVAILABLE = False
    _ANTHROPIC_AVAILABLE = False

try:
    import anthropic  # get_gemini_rush_hour_recommendation의 예외 처리(APIStatusError)용
except ImportError:
    anthropic = None

# elderly 브랜치에서 온, 과거 승하차 통계(bus_ridership.csv) 기반 혼잡도 모듈.
# 위 services.general_route(GBIS 실시간 여석 기반, 일반 모드용)와는 별개로,
# 교통약자 모드의 혼잡도 점수 계산과 "운행 안 하는 버스" 필터링에 씁니다.
# 이름이 같은 함수가 있어서(get_bus_occupancy_for_route) 별칭을 붙여 가져옵니다.
try:
    from bus_ridership_congestion import (
        get_bus_occupancy_for_route as get_bus_occupancy_for_route_hist,
        get_bus_congestion_trend_for_route,
        preload_bus_ridership,
        route_has_non_operating_bus,
    )
    ACCESSIBILITY_CONGESTION_AVAILABLE = True
except ImportError as e:
    print(f"[안내] bus_ridership_congestion 모듈을 찾을 수 없어 교통약자 모드의 과거 통계 기반 혼잡도 기능은 비활성화됩니다: {e}")
    ACCESSIBILITY_CONGESTION_AVAILABLE = False

from subway_guide import (
    SERVICE_KEY,
    fetch_quick_get_off_info,
    list_covered_stations,
    get_transfer_tips_for_route,
)

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False

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
    """노약자 모드: 도보 시간을 최우선으로 최소화 (환승 시 도보 이동도
    walk_time_total_min에 이미 포함되어 있어서 별도 기준으로 안 둡니다)."""
    return (
        route.get("walk_time_total_min", 0),
        route.get("estimated_comfort_time_min", 0),
    )


def _min_time_score(route):
    """순수하게 '가장 빨리 도착하는' 경로를 고를 때 씁니다 (혼잡도·광역버스 등 다른
    조건은 전혀 안 보고, 예상 소요시간만 봅니다) — '최소 시간' 카테고리용."""
    return route.get("estimated_comfort_time_min", 0)


_CONGESTION_LEVEL_SCORE = {"혼잡": 2, "보통": 1, "여유": 0}


# =============================================================================
# 지하철 혼잡도 (서울교통공사 실제 데이터, subway_congestion.csv) — elderly 브랜치
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


def get_route_subway_congestion_list(route, weekday, hour, minute):
    """경로에 있는 지하철 구간들 각각의 "지금 혼잡도(%)"를 반환합니다.
    (버스의 get_bus_occupancy_for_route_hist와 같은 역할 — 오르내림과 무관하게,
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


def _subway_congestion_label(pct):
    """서울교통공사가 실제로 쓰는 구간 기준으로 혼잡도 % → 등급 문자열 환산."""
    if pct < 34:
        return "여유"
    if pct < 80:
        return "보통"
    return "혼잡"


def get_route_subway_congestion_trend(route, weekday, hour, minute):
    """경로에 있는 지하철 구간들 중, 지금 혼잡도와 "다음 30분 슬롯" 혼잡도가
    다른(오르거나 내리는) 구간을 골라서 반환합니다. 각 항목은
    {line, station, current_pct, next_pct, diff_pct, direction, minutes_until_next}
    형태입니다 (diff_pct는 양수=상승, 음수=하락, direction은 "up"/"down").

    검색 시각을 모든 구간에 그대로 쓰지 않고, 그 구간 앞에 있는 도보·지하철·
    버스 구간들의 소요시간을 누적해서 "실제로 그 구간에 도달하는 시각"을 구해
    사용합니다. "몇 분 후면 혼잡도가 몇 % 더 오르는지/내려가는지" 안내 문구를
    만드는 데 씁니다. 혼잡도가 그대로거나 데이터가 없는 구간은 결과에서 빠집니다."""
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

                        # 오르든 내리든, 다음 지하철이 10분 이내에 올 때만 안내합니다.
                        if diff_pct > 0 and minutes_until_next <= 10:
                            trends.append({
                                "line": line,
                                "station": station,
                                "current_pct": round(cur_pct),
                                "next_pct": round(next_pct),
                                "diff_pct": round(diff_pct),
                                "direction": "up",
                                "minutes_until_next": minutes_until_next,
                                "recommendation": "지금 이동하는 것을 추천합니다",
                            })
                        elif diff_pct < 0 and minutes_until_next <= 10:
                            trends.append({
                                "line": line,
                                "station": station,
                                "current_pct": round(cur_pct),
                                "next_pct": round(next_pct),
                                "diff_pct": round(diff_pct),
                                "direction": "down",
                                "minutes_until_next": minutes_until_next,
                                "recommendation": f"{minutes_until_next}분 후에 이동하는 것을 추천합니다",
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


def _subway_pct_to_score(pct):
    """지하철 혼잡도(%) → 0(여유)~2(혼잡) 점수 환산 (서울교통공사가 실제로 쓰는 구간 기준)."""
    if pct < 34:
        return 0
    if pct < 80:
        return 1
    return 2


def _route_congestion_score(route, weekday=0, hour=9, minute=0):
    """경로 전체의 "평균" 혼잡도 점수 (0=여유 ~ 2=혼잡).
    경로 안에 있는 지하철 구간(subway_congestion.csv 기반)과 버스 구간
    (bus_ridership.csv 기반)을 전부 모아서, 대중교통 구간별 혼잡도 점수의
    평균을 냅니다 — 지하철이 있으면 지하철만 보는 게 아니라, 버스 구간이
    같이 있으면 그것도 같이 반영합니다. 도보 구간은 계산에서 빠집니다.
    데이터가 하나도 없으면 0(=모름, 순위에 영향 안 줌)을 반환합니다."""
    scores = []

    for entry in get_route_subway_congestion_list(route, weekday, hour, minute):
        scores.append(_subway_pct_to_score(entry["current_pct"]))

    if ACCESSIBILITY_CONGESTION_AVAILABLE:
        occupancy = get_bus_occupancy_for_route_hist(route.get("sub_paths", []), hour=hour, minute=minute)
        for o in occupancy:
            scores.append(_CONGESTION_LEVEL_SCORE.get(o.get("congestion"), 1))

    if not scores:
        return 0
    return sum(scores) / len(scores)


def _build_ai_route_reason(accessibility_type, elevator_found, congestion_checked):
    """AI 추천 경로 3개에 붙일, 고정된 짧은 안내 문구를 반환합니다."""
    if accessibility_type == "pregnant":
        return "혼잡도가 낮고 편안하게 이동할 수 있는 경로입니다."
    elif accessibility_type == "elderly":
        return "도보 이동과 계단 이용을 최소화한 경로입니다."
    return "환승과 도보가 적은 경로입니다."


def select_accessibility_routes(routes, accessibility_type=None, weekday=0, hour=9, minute=0, rush_hour=False):
    """노약자/임산부 모드에서 화면에 보여줄 경로 5개를 고릅니다.

    accessibility_type에 따라 앞 3개(burden 카테고리)를 고르는 기준이 달라집니다:
      - "elderly" (노약자): 도보 최소화 → 종착지 엘리베이터 유무 순
      - "pregnant" (임산부): 혼잡도(서울 버스·지하철) 최소화 → 종착지 엘리베이터
        유무 → 도보 최소화 순
      - 그 외("both" 포함) / None: 환승 → 도보 순 (기본값)

    - 뒤 2개: 남은 경로 중 요금이 가장 저렴한 것 1개("최소 금액"),
      그리고 예상 소요시간이 가장 짧은 것 1개("최소 시간", 다른 조건은 안 봄).

    각 경로 dict에 category("burden"/"cost"/"time")와 그에 맞는
    category_label(화면에 보여줄 탭 이름)을 붙여서 반환합니다. burden 카테고리는
    선택 기준 자체는 시간대와 무관하게 항상 동일하게 적용되지만, 화면에 "AI 추천"
    이라고 부르는 건 실제로 Claude가 이 경로를 분석해주는 러시아워 시간대에만
    맞는 표현이라(하단 rush_hour_result 배너와 짝을 맞춰야 함 — 안 그러면 "지금은
    AI 추천이 제공되지 않아요" 안내랑 위쪽 탭 이름이 "AI 추천 경로"라서 서로
    모순돼 보이는 문제가 있었음), rush_hour가 아닐 땐 그냥 "추천 경로 N"으로
    라벨링합니다. "burden" 카테고리에는 무슨 기준으로 뽑혔는지 설명하는
    ai_reason 문구도 같이 붙입니다.
    이미 계산한 엘리베이터 정보는 "_elevator_info_cache"에 담아두는데,
    호출한 쪽(get_optimal_route)에서 이 캐시를 그대로 재사용하고 지워야 합니다.
    """
    if not routes:
        return []

    # route_finder가 mode="accessibility"로 이미 (환승, 도보, 시간) 순 정렬해서 주지만,
    # accessibility_type에 따라 기준이 다르면 여기서 다시 정렬합니다. 이 1차 정렬은
    # "상위 후보 8개"를 뽑기 위한 저렴한 근사치일 뿐이고, 최종 순위(엘리베이터·혼잡도
    # 반영)는 바로 아래 shortlist 재정렬에서 정확하게 다시 매겨집니다.
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

    congestion_checked = False
    if accessibility_type == "pregnant":
        # 임산부 모드: 혼잡도 → 엘리베이터 → 도보 순으로 최종 재정렬합니다.
        congestion_checked = True
        for r in shortlist:
            r["_congestion_score_cache"] = _route_congestion_score(r, weekday=weekday, hour=hour, minute=minute)

        def _shortlist_score(r):
            info = r.get("_elevator_info_cache")
            elevator_found = bool(info and info.get("directions"))
            return (
                r.get("_congestion_score_cache", 0),  # 1순위: 혼잡도 낮은 쪽
                0 if elevator_found else 1,            # 2순위: 엘리베이터 확인 여부
                r.get("walk_time_total_min", 0),       # 3순위: 도보 시간
                r.get("estimated_comfort_time_min", 0),
            )
    elif accessibility_type == "elderly":
        # 노약자 모드: 도보 → 엘리베이터 순으로 최종 재정렬합니다.
        def _shortlist_score(r):
            info = r.get("_elevator_info_cache")
            elevator_found = bool(info and info.get("directions"))
            return (
                r.get("walk_time_total_min", 0),  # 1순위: 도보 시간
                0 if elevator_found else 1,        # 2순위: 엘리베이터 확인 여부
                r.get("estimated_comfort_time_min", 0),
            )
    else:
        def _shortlist_score(r):
            info = r.get("_elevator_info_cache")
            elevator_found = bool(info and info.get("directions"))
            return (
                r.get("transfer_count", 0),
                r.get("walk_time_total_min", 0),
                0 if elevator_found else 1,
                r.get("estimated_comfort_time_min", 0),
            )

    shortlist.sort(key=_shortlist_score)

    burden_routes = shortlist[:3]
    chosen_ids = {id(r) for r in burden_routes}

    true_cheapest = min(routes, key=lambda r: r.get("payment_krw", 0))
    cost_route = None if id(true_cheapest) in chosen_ids else true_cheapest
    if cost_route is not None:
        chosen_ids.add(id(cost_route))

    true_min_time = min(routes, key=_min_time_score)
    min_time_route = None if id(true_min_time) in chosen_ids else true_min_time

    result = []
    for i, r in enumerate(burden_routes):
        info = r.get("_elevator_info_cache")
        elevator_found = bool(info and info.get("directions"))
        r["category"] = "burden"
        r["category_label"] = f"AI 추천 경로 {i + 1}" if rush_hour else f"추천 경로 {i + 1}"
        r["ai_reason"] = _build_ai_route_reason(accessibility_type, elevator_found, congestion_checked)
        result.append(r)
    if cost_route is not None:
        cost_route["category"] = "cost"
        cost_route["category_label"] = "최소 금액"
        result.append(cost_route)
    if min_time_route is not None:
        min_time_route["category"] = "time"
        min_time_route["category_label"] = "최소 시간"
        result.append(min_time_route)

    # 응답에 안 실어도 되는 내부 계산용 캐시는 정리합니다 (엘리베이터 캐시는
    # 호출한 쪽에서 재사용하니 남겨두고, 혼잡도 캐시만 지웁니다).
    for r in result:
        r.pop("_congestion_score_cache", None)

    return result


def _has_congestion_risk(route):
    """경로 안에 실제로 만석 위험이 있는 버스 구간이 있는지 확인합니다.
    get_bus_occupancy_for_route로 sub_paths에 bus_congestion을 미리 채워둔
    경로에만 의미가 있고, 아직 안 채워졌으면(정보 없음) 위험 없음으로 취급합니다."""
    for leg in route.get("sub_paths", []):
        if leg.get("traffic_type") != 2:
            continue
        congestion = leg.get("bus_congestion")
        if congestion and congestion.get("level") in ("혼잡", "매우혼잡"):
            return True
    return False


def select_general_routes(routes):
    """일반 모드에서 화면 탭에 보여줄 경로 최대 6개를, 각각 다른 기준으로 고릅니다.

    여유로는 "그냥 가장 빠른 경로"를 보여주는 일반 지도앱과 다르게, 시간이 조금
    더 걸리더라도 실제로 탈 수 있고 쾌적한 경로를 우선 보여주는 게 목표입니다.
    그래서 AI 쪽과 일반 쪽의 기준을 아예 다르게 나눴습니다 — 겹치는 기준이 없어야
    "AI가 판단한 게 일반 경로랑 뭐가 다른가"가 실제로 드러납니다.

    - AI 러시아워 3개 (쾌적/탑승 가능성 중심 — 만석 위험이 있는 버스 구간이 낀
      경로는 아예 후보에서 제외합니다. 사람들이 환승을 더 하느니 차라리 확실히
      탈 수 있는 경로를 원할 거라는 판단):
      "AI 추천 경로" — estimated_comfort_time_min(광역버스 러시아워 지연 페널티까지
        반영된 체감 소요시간) 기준. 그냥 빠른 게 아니라 실제 상황을 반영한 최적.
      "여유 경로" — 만석 위험이 큰 광역버스(has_express_bus)에 아예 의존하지 않는
        경로 중 가장 빠른 것. 시간이 더 걸려도 확실히 탈 수 있는 경로.
      "최소 환승" — transfer_count 최소.
    - 일반 경로 3개 (스펙만 보는, 다른 지도앱과 같은 기준 — 혼잡 위험 여부와
      무관하게 순수 스펙만 봄):
      "최소 시간"(original_time_min, 쾌적함 고려 없는 순수 소요시간),
      "최소 금액"(payment_krw), "최소 도보"(walk_time_total_min).

    같은 경로가 여러 기준에서 동시에 1등이면 중복 없이 다음 후보로 넘어갑니다.
    후보가 부족하면 그만큼 적게 반환합니다 (억지로 6개를 채우지 않음).

    호출 전에 app.py에서 bus_congestion을 이미 채워둬야 혼잡 위험 필터가
    제대로 동작합니다 — 안 채워져 있으면 _has_congestion_risk가 항상 False라서
    그냥 필터 없이 고르는 것과 같습니다.
    """
    if not routes:
        return []

    chosen_ids = set()
    result = []

    def pick(label, key_func, category, filter_func=None, strict_filter_func=None):
        candidates = [r for r in routes if id(r) not in chosen_ids]
        if strict_filter_func:
            # 반드시 지켜야 하는 조건(혼잡 위험 회피 등) — 만족하는 후보가 하나도
            # 없으면 "그나마 나은 걸로" 폴백하지 않고 이 카테고리는 그냥 건너뜀.
            # (예전엔 여기도 물러서는 필터였어서, 후보 10개가 전부 위험한 버스에
            # 의존하는 구간이면 결국 위험한 경로가 "여유 경로"로 뽑히는 문제가 있었음)
            candidates = [r for r in candidates if strict_filter_func(r)]
            if not candidates:
                return
        if filter_func:
            # 이건 "가능하면 지키고 싶은" 선호 조건 — 조건에 맞는 후보가 있으면 그
            # 안에서만 고르고, 하나도 없으면 (엄격 조건은 이미 위에서 통과했으므로)
            # 어쩔 수 없이 남은 후보 중에서 고름.
            narrowed = [r for r in candidates if filter_func(r)]
            if narrowed:
                candidates = narrowed
        if not candidates:
            return
        best = min(candidates, key=key_func)
        chosen_ids.add(id(best))
        best["category"] = category
        best["category_label"] = label
        result.append(best)

    no_risk = lambda r: not _has_congestion_risk(r)
    no_express = lambda r: not r.get("has_express_bus", False)

    # AI 러시아워 3개 — 쾌적/탑승 가능성 중심. 혼잡 위험 있는 경로는 무조건 제외
    # (strict_filter_func) — 안전한 후보가 없으면 그 자리는 그냥 안 채움.
    pick("AI 추천 경로", lambda r: r.get("estimated_comfort_time_min", 0), "ai_optimal", strict_filter_func=no_risk)
    pick(
        "여유 경로",
        lambda r: r.get("estimated_comfort_time_min", 0),
        "ai_comfortable",
        strict_filter_func=no_risk,
        filter_func=no_express,  # 광역버스 자체를 안 타면 더 좋지만, 안전하기만 하면 완화 가능
    )
    pick("최소 환승", lambda r: r.get("transfer_count", 0), "ai_fewest_transfer", strict_filter_func=no_risk)

    # 일반 경로 3개 — 스펙만 보는 기준 (쾌적함/혼잡위험 고려 없음, 다른 지도앱과 같은 관점)
    pick("최소 시간", lambda r: r.get("original_time_min", 0), "general_fastest")
    pick("최소 금액", lambda r: r.get("payment_krw", 0), "general_cheapest")
    pick("최소 도보", lambda r: r.get("walk_time_total_min", 0), "general_least_walk")

    return result


def _is_weekend_ish(weekday):
    """weekday: 0=월 ... 6=일 (App.tsx의 currentWeekday 변환과 동일한 규칙).
    금/토/일은 밤 시간대에도 러시아워 한 타임이 더 있음."""
    return weekday in (4, 5, 6)  # 금, 토, 일

def is_rush_hour(hour, minute, weekday):
    """AI 러시아워 추천(AI 추천 경로/여유 경로/최소 환승)이 적용되는 시간대인지
    판단합니다. 이 시간대가 아니면 일반 지도앱처럼 그냥 스펙 기준 경로만 나갑니다.

    - 월~목: 새벽 05:30~07:30, 오후 16:30~19:30
    - 금~일: 새벽 05:30~07:30, 오후 16:30~19:30, 밤 21:00~23:00 (한 타임 더)
    """
    total_min = hour * 60 + minute
    morning = 5 * 60 + 30 <= total_min <= 7 * 60 + 30
    evening = 16 * 60 + 30 <= total_min <= 19 * 60 + 30
    if morning or evening:
        return True
    if _is_weekend_ish(weekday) and 21 * 60 <= total_min <= 23 * 60:
        return True
    return False

def get_weekday_korean(weekday):
    days = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return days[weekday] if 0 <= weekday <= 6 else "평일"

def get_rush_hour_type(hour, minute, weekday):
    total_min = hour * 60 + minute
    if 5 * 60 + 30 <= total_min <= 7 * 60 + 30:
        return "출근 러시아워"
    elif 16 * 60 + 30 <= total_min <= 19 * 60 + 30:
        return "퇴근 러시아워"
    elif _is_weekend_ish(weekday) and 21 * 60 <= total_min <= 23 * 60:
        return "심야 러시아워"
    return "러시아워"

def get_gemini_rush_hour_recommendation(routes, start, end, hour, minute, weekday, elevator_info=None, accessibility_type=None, transfer_info=None):
    """이름은 예전 Gemini 시절 그대로 유지했지만(앱 코드에서 이 이름으로 호출함)
    실제로는 Claude(Anthropic API)를 호출함 — 일반 모드의
    get_gemini_general_recommendation과 같은 이유로 교체(Gemini 무료 할당량 0).
    프롬프트 내용(엘리베이터/환승/노약자·임산부 안내문 등)은 전혀 손대지 않고
    호출 방식(HTTP 요청 → Anthropic SDK)과 에러 처리만 바꿈."""
    if not _ANTHROPIC_AVAILABLE or not os.environ.get("ANTHROPIC_API_KEY"):
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

        client = _get_anthropic_client()
        response = client.messages.create(
            model=CLAUDE_RUSH_HOUR_MODEL,
            max_tokens=500,
            output_config={"format": {"type": "json_schema", "schema": _RUSH_HOUR_TIP_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        return json.loads(text)

    except anthropic.APIStatusError as e:
        print(f"[Claude] 호출 실패 ({e.status_code}): {e.message}", flush=True)
        return {
            "recommended_index": 0,
            "rush_hour_tip": f"분석 중 오류: Claude API {e.status_code} - {e.message}",
            "alternative": "",
        }
    except Exception as e:
        print(f"[Claude] 예외 발생: {e}", flush=True)
        return {
            "recommended_index": 0,
            "rush_hour_tip": f"분석 중 오류: {str(e)}",
            "alternative": ""
        }


BUS_LIST_KEY = "4e6c484353746966383074764c4452"   # busRteInfo용
BUS_ARR_KEY = "c1UVTmspAy1%2F9y1h9T41mvJEUqQ5265VeC79svxBTHqBP1RBZTDXbuXGR4yHeJY8q%2BDgLKT8oq2ROnMzxO6d%2Fg%3D%3D"  # getArrInfoByRouteAllList용

_route_cache = {}     # { "140": "100100118", ... }
_route_cache_time = 0
CACHE_TTL = 60 * 60 * 6  # 6시간마다 갱신

def build_route_cache():
    global _route_cache, _route_cache_time
    mapping = {}
    start = 1
    page_size = 1000
    while True:
        end = start + page_size - 1
        url = f"http://openapi.seoul.go.kr:8088/{BUS_LIST_KEY}/json/busRteInfo/{start}/{end}/"
        res = requests.get(url, timeout=10).json()
        rows = res.get("busRteInfo", {}).get("row", [])
        if not rows:
            break
        for r in rows:
            mapping[r["RTE_NM"]] = r["ROUTE_ID"]
        if len(rows) < page_size:
            break
        start += page_size
    _route_cache = mapping
    _route_cache_time = time.time()

def get_route_id(route_nm: str):
    global _route_cache_time
    if not _route_cache or (time.time() - _route_cache_time > CACHE_TTL):
        build_route_cache()
    return _route_cache.get(route_nm)

CONGESTION_MAP = {"0": "정보없음", "3": "여유", "4": "보통", "5": "혼잡"}


@app.route("/bus/search")
def bus_search():
    route_nm = request.args.get("routeNm", "").strip()
    city_code = request.args.get("cityCode", "").strip() or None
    if not route_nm:
        return jsonify({"error": "버스 번호를 입력해주세요."}), 400

    result = get_route_congestion(route_nm, city_code=city_code)

    if result.get("error"):
        return jsonify({"error": result["error"], "routes": []}), 404

    stations = [
        {
            "stationName": s["stationName"],
            "congestionLevel": s["congestionLevel"] or "0",
            "congestionLabel": s["congestionLabel"],
            "isFull": s["congestionLevel"] == "5",
        }
        for s in result["stations"]
    ]

    return jsonify({
        "routes": [{
            "routeId": result["routeId"],
            "routeNm": result["routeNo"],
            "direction": "전체",
            "stations": stations,
        }]
    })

@app.route("/api/congestion/route")
def bus_congestion_route():
    """서울 통계 기반(csv) 버스 혼잡도 - 노선번호로 검색 -> 경유 정류소 전체 + 혼잡도"""
    route_nm = request.args.get("routeNm", "").strip()
    hour = request.args.get("hour", type=int)

    if not route_nm:
        return jsonify({"error": "버스 번호를 입력해주세요."}), 400

    if hour is None:
        from datetime import datetime
        hour = datetime.now().hour

    stops = est.get_route_stops(route_nm, hour=hour)

    if not stops:
        return jsonify({"error": f"'{route_nm}'번 노선을 찾을 수 없습니다.", "routes": []}), 404

    stations = [
        {
            "stationName": s["역명"],
            "stopId": s["정류장ID"],
            "arsNumber": s["ARS번호"],
            "level": s["level"],           # "여유" | "보통" | "혼잡"
            "barPercent": s["bar_percent"],  # 0~100
            "avgCount": s["count"],          # 하루평균 승차인원(참고용)
        }
        for s in stops
    ]

    return jsonify({
        "source": "stats",  # 프론트에서 실시간(gbis)과 구분하기 위한 표시
        "routes": [{
            "routeNm": stops[0]["노선번호"],
            "routeName": stops[0]["노선명"],
            "hour": hour,
            "stations": stations,
        }]
    })



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

    # 검색 시각 기준으로, 경로 안에 운행하지 않는 버스가 있으면 그 경로는 후보에서
    # 제외합니다 (예: 심야버스를 낮 시간대에 타야 하는 경로 등). 승하차 데이터가
    # 아예 없는 조합은 판단 불가로 보고 걸러내지 않습니다.
    if ACCESSIBILITY_CONGESTION_AVAILABLE and routes:
        routes = [
            r for r in routes
            if not route_has_non_operating_bus(r.get("sub_paths", []), hour, minute)
        ]
        final_result["routes"] = routes

    rush_hour = is_rush_hour(hour, minute, weekday)
    rush_hour_result = None

    # 노약자 모드: 도보 구간 시간을 실제 체감 속도에 맞게 늘립니다.
    # (정렬/선택보다 먼저 적용해야, 늘어난 도보 시간 기준으로 "AI 추천 경로"가 뽑혀요.)
    if accessibility_type == "elderly" and routes:
        routes = apply_elderly_walk_time(routes)

    # 교통약자 모드일 땐 후보 전체가 아니라, 부담 최소 3개 + 최소금액/최소시간 2개로
    # 화면에 보여줄 경로를 5개로 좁혀서 확정합니다. (accessibility_type에 따라
    # 노약자는 도보 최소화, 임산부는 혼잡도 최소화 기준으로 다르게 고릅니다.)
    if mode != 'general' and routes:
        routes = select_accessibility_routes(
            routes, accessibility_type=accessibility_type, weekday=weekday, hour=hour, minute=minute,
            rush_hour=rush_hour,
        )
        final_result["routes"] = routes
    # 일반 모드는 "AI 러시아워 추천"이 실제 러시아워 시간대에만 적용되도록 합니다.
    # 러시아워가 아니면 AI 추천/여유 경로 같은 카테고리 없이 그냥 원래 정렬 순서
    # (광역버스 우선 + 최소시간) 상위 6개만 보여줍니다 — category_label을 안 붙이면
    # 프론트가 전부 "일반 경로 N"으로 표시합니다.
    elif mode == 'general' and routes:
        if rush_hour:
            if GENERAL_ROUTE_AVAILABLE:
                # select_general_routes가 "혼잡 위험 있는 경로는 AI 추천에서 제외"하려면
                # 고르기 전에 여석 정보가 이미 채워져 있어야 함 — 그래서 여기서 (예전엔
                # 최종 선택된 6개에 대해서만 하던 걸) 더 넓은 후보 풀에 대해 먼저 계산함.
                # 안전한 후보를 못 찾으면 그 카테고리를 그냥 건너뛰게 바꿨기 때문에
                # (혼잡한 경로로 대충 채우지 않음), 후보 폭이 너무 좁으면 AI 추천
                # 자리가 자주 비게 됨 — 그래서 15개까지 넓힘. 후보 전체(최대 20개
                # 안팎)를 다 하면 느려지니 comfort_time 기준으로 이미 정렬돼 있는
                # 상위 15개로 제한. 같은 정류소/버스 조합은 bus_congestion_cache로
                # 재사용해서 GBIS 중복 호출도 줄임.
                bus_congestion_cache = {}
                candidate_pool = routes[:15]
                for r in candidate_pool:
                    r["sub_paths"] = get_bus_occupancy_for_route(
                        r.get("sub_paths", []), cache=bus_congestion_cache
                    )
                routes = select_general_routes(candidate_pool)
            else:
                routes = select_general_routes(routes)
        else:
            routes = routes[:6]
        final_result["routes"] = routes

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
            # 버스 구간의 과거 승하차 통계 기반 혼잡도 — 교통약자 모드 보조 정보
            # (일반 모드의 GBIS 실시간 여석 정보와는 별개의, 시간대별 평균 데이터입니다)
            if ACCESSIBILITY_CONGESTION_AVAILABLE:
                bus_occupancy_list.append(
                    get_bus_occupancy_for_route_hist(r.get("sub_paths", []), hour=hour, minute=minute)
                )
                bus_congestion_trend_list.append(
                    get_bus_congestion_trend_for_route(r.get("sub_paths", []), hour, minute)
                )
            else:
                bus_occupancy_list.append([])
                bus_congestion_trend_list.append([])
            # 지하철 구간별 "지금 평균 혼잡도"와 "다음 30분 뒤 혼잡도 변화" 안내
            subway_congestion_list.append(get_route_subway_congestion_list(r, weekday, hour, minute))
            subway_congestion_trend_list.append(get_route_subway_congestion_trend(r, weekday, hour, minute))

    first_route_first_transfer = (
        transfer_info_list[0][0] if transfer_info_list and transfer_info_list[0] else None
    )

    if rush_hour and routes:
        if mode == 'general':
            if GENERAL_ROUTE_AVAILABLE:
                # 여석 정보는 위(select_general_routes 호출 전)에서 후보 풀 단계에서
                # 이미 다 채워놨음 — 최종 선택된 routes는 그 후보 풀의 부분집합이라
                # sub_paths.bus_congestion이 그대로 들어있음.
                # get_gemini_general_recommendation은 occupancy_data가
                # [{routeName, station, label}, ...] 형태이길 기대함 — 위에서
                # sub_paths에 채운 bus_congestion 필드로부터 그 형태를 다시 만들어줌
                # (sub_paths 자체를 그대로 넘기면 도보/지하철 구간엔 routeName이
                # 없어서 KeyError로 500이 났었음).
                occupancy_data = [
                    {
                        "routeName": leg.get("lane_name"),
                        "station": leg.get("start_name"),
                        "label": leg["bus_congestion"]["label"],
                    }
                    for leg in routes[0].get("sub_paths", [])
                    if leg.get("traffic_type") == 2 and leg.get("bus_congestion")
                ]
                rush_hour_result = get_gemini_general_recommendation(
                    routes, occupancy_data, start, end, hour, minute, weekday
                )
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
    final_result["elevator_info_list"] = elevator_info_list
    final_result["transfer_info_list"] = transfer_info_list
    # bus_occupancy_list / bus_congestion_trend_list / subway_congestion_list /
    # subway_congestion_trend_list: 교통약자 모드에서 경로별 과거 통계 기반 혼잡도
    # 보조 정보 (bus_ridership_congestion 모듈 없으면 전부 빈 배열).
    final_result["bus_occupancy_list"] = bus_occupancy_list
    final_result["bus_congestion_trend_list"] = bus_congestion_trend_list
    final_result["subway_congestion_list"] = subway_congestion_list
    final_result["subway_congestion_trend_list"] = subway_congestion_trend_list

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
    if ACCESSIBILITY_CONGESTION_AVAILABLE:
        preload_bus_ridership()  # 서버 뜨기 전에 미리 로딩 (첫 검색이 느려지는 것 방지)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)