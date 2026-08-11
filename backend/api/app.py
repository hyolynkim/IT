from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import sys
import requests, time
import json
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
    from services.general_route import get_bus_occupancy_for_route, get_gemini_general_recommendation  # ⬅️ 추가
    GENERAL_ROUTE_AVAILABLE = True
except ModuleNotFoundError as e:
    print(f"[안내] services.general_route 모듈을 찾을 수 없어 '일반인 모드'는 비활성화됩니다: {e}")
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
    """환승·도보가 적을수록(=몸에 부담이 적을수록) 작은 값. 노약자 모드 기본 정렬 기준."""
    return (
        route.get("transfer_count", 0),
        route.get("walk_time_total_min", 0),
        route.get("estimated_comfort_time_min", 0),
    )


def _general_optimal_score(route):
    """일반 모드와 동일한 기준(광역버스 우선 → 시간 순)으로 '가장 빠른/좋은' 경로를 고를 때 씁니다."""
    return (not route.get("has_express_bus", False), route.get("estimated_comfort_time_min", 0))


def select_accessibility_routes(routes):
    """노약자/임산부 모드에서 화면에 보여줄 경로 5개를 고릅니다.

    - 앞 3개("부담 최소"): 환승·도보가 가장 적은 경로. 그중에서도 동점이면
      엘리베이터 하차 위치가 실제로 확인되는 경로를 우선합니다.
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
    # 여기서도 명시적으로 한 번 더 정렬해 안전하게 갑니다.
    burden_sorted = sorted(routes, key=_walk_burden_score)

    # 부담이 비슷한 상위 후보들 중, 엘리베이터 정보가 실제로 확인되는 경로를
    # 우선하기 위해 상위 몇 개만 미리 엘리베이터 조회를 해둡니다 (전체를 다 조회하면
    # 공공데이터 API를 너무 많이 호출하게 돼서, 상위 후보로 범위를 좁혔어요).
    shortlist = burden_sorted[:8]
    for r in shortlist:
        r["_elevator_info_cache"] = get_elevator_tip_for_route(r)

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

    return result


def _general_ai_optimal_score(route):
    """일반 모드 'AI 추천' 1번 — 광역버스 포함 여부 우선, 그다음 소요시간 순."""
    return (not route.get("has_express_bus", False), route.get("estimated_comfort_time_min", 0))


def select_general_routes(routes):
    """일반 모드에서 화면 탭에 보여줄 경로 최대 6개를, 각각 다른 기준으로 고릅니다.

    - AI 러시아워 3개: "AI 추천"(광역버스 우선+최소시간), "최소 시간", "최소 환승"
    - 일반 경로 3개: "최소 금액", "최소 도보", "최소 환승"
    같은 경로가 여러 기준에서 동시에 1등이면(예: 최소시간 경로가 곧 최소환승 경로이기도 함)
    중복 없이 다음으로 그 기준에 맞는 경로를 대신 고릅니다 — 그래서 실제로 화면에
    최대 6개의 서로 다른 경로가 각자의 강점과 함께 나타납니다. 후보가 부족하면
    그만큼 적게 반환합니다 (억지로 6개를 채우지 않음).
    """
    if not routes:
        return []

    chosen_ids = set()
    result = []

    def pick(label, key_func, category):
        candidates = [r for r in routes if id(r) not in chosen_ids]
        if not candidates:
            return
        best = min(candidates, key=key_func)
        chosen_ids.add(id(best))
        best["category"] = category
        best["category_label"] = label
        result.append(best)

    # AI 러시아워 3개
    pick("AI 추천 경로", _general_ai_optimal_score, "ai_optimal")
    pick("최소 시간", lambda r: r.get("estimated_comfort_time_min", 0), "ai_fastest")
    pick("최소 환승", lambda r: r.get("transfer_count", 0), "ai_fewest_transfer")

    # 일반 경로 3개
    pick("최소 금액", lambda r: r.get("payment_krw", 0), "general_cheapest")
    pick("최소 도보", lambda r: r.get("walk_time_total_min", 0), "general_least_walk")
    pick("최소 환승", lambda r: r.get("transfer_count", 0), "general_fewest_transfer")

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

    # 교통약자 모드일 땐 후보 전체가 아니라, 부담 최소 3개 + 최소금액/최적 2개로
    # 화면에 보여줄 경로를 5개로 좁혀서 확정합니다.
    if mode != 'general' and routes:
        routes = select_accessibility_routes(routes)
        final_result["routes"] = routes
    # 일반 모드도 마찬가지로, 그냥 시간순 상위 N개가 아니라 "AI 추천/최소시간/최소환승"
    # + "최소금액/최소도보/최소환승" 각각 다른 기준의 대표 경로 최대 6개로 좁힙니다.
    elif mode == 'general' and routes:
        routes = select_general_routes(routes)
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
    if mode != 'general' and routes:
        for r in routes[:10]:
            cached = r.pop("_elevator_info_cache", _MISSING)
            elevator_info_list.append(get_elevator_tip_for_route(r) if cached is _MISSING else cached)
            transfer_info_list.append(get_transfer_tips_for_route(r))

    first_route_first_transfer = (
        transfer_info_list[0][0] if transfer_info_list and transfer_info_list[0] else None
    )

    if rush_hour and routes:
        if mode == 'general':
            if GENERAL_ROUTE_AVAILABLE:
                # 화면 탭에 보이는 후보 경로(AI 러시아워 3개 + 일반 경로 3개 = 최대 6개,
                # 프론트 RouteResultScreen의 list.slice(0, 6)과 맞춤)에 여석 정보를 채워줍니다.
                # (예전엔 routes[0]에만 채워서 다른 경로 탭을 선택하면 여석 뱃지가 안 보였음)
                # 같은 정류소/버스 조합은 bus_congestion_cache로 재사용해 GBIS 중복 호출을
                # 줄임 — 안 그러면 경로 수 × 버스 구간 수만큼 순차 호출이 쌓여 타임아웃 남.
                bus_congestion_cache = {}
                for r in routes[:6]:
                    r["sub_paths"] = get_bus_occupancy_for_route(
                        r.get("sub_paths", []), cache=bus_congestion_cache
                    )
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
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)