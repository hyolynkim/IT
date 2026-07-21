from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re
import sys
import requests
import json
from dataclasses import asdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from models.route_finder import find_cat_optimal_route

try:
    from services.general_route import get_bus_occupancy_for_route, get_gemini_general_recommendation  # ⬅️ 추가
    GENERAL_ROUTE_AVAILABLE = True
except ModuleNotFoundError as e:
    print(f"[안내] services.general_route 모듈을 찾을 수 없어 '일반인 모드'는 비활성화됩니다: {e}")
    GENERAL_ROUTE_AVAILABLE = False

from subway_elevator_guide import (
    SERVICE_KEY,
    fetch_quick_get_off_info,
    list_covered_stations,
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

    return {
        "line": info.line,
        "station": info.station,
        "directions": [asdict(d) for d in info.directions],
    }

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

def get_gemini_rush_hour_recommendation(routes, start, end, hour, minute, weekday, elevator_info=None, accessibility_type=None):
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

        # 노약자/임산부 여부에 따라 Gemini가 실제로 배려한 경로를 추천하도록 안내문 추가
        accessibility_note = ""
        if accessibility_type == "pregnant":
            accessibility_note = (
                "\n6. 이 이용자는 임산부입니다. 계단·에스컬레이터보다 엘리베이터 동선을, "
                "환승 횟수가 적은 경로를 우선 고려하고, 혼잡이 심한 구간·시간대는 피하도록 추천해주세요."
            )
        elif accessibility_type == "elderly":
            accessibility_note = (
                "\n6. 이 이용자는 노약자입니다. 도보 이동 거리와 환승 횟수가 적은 경로를 우선하고, "
                "무리한 급행 환승보다는 여유 있게 갈 수 있는 동선을 추천해주세요."
            )
        elif accessibility_type == "both":
            accessibility_note = (
                "\n6. 이 이용자는 노약자 및 임산부를 위한 경로가 필요합니다. 계단·도보 이동과 환승을 "
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
4. {weekday_str} {hour}시의 실제 교통 패턴 반영{elevator_note}{accessibility_note}

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

    final_result = find_cat_optimal_route(start, end, hour)

    if final_result.get("status") == "fail":
        return jsonify(final_result)

    routes = final_result.get("routes", [])
    rush_hour = is_rush_hour(hour, minute, weekday)
    rush_hour_result = None

    # 교통약자 모드(mode != 'general')일 때만 엘리베이터 인접 하차칸 정보를 조회합니다.
    # (러시아워 여부와 상관없이 항상 계산 — 엘리베이터 위치는 혼잡도와 무관한 정보라서요)
    # 화면에서 사용자가 다른 경로를 선택할 수 있으므로, 상위 경로들 각각에 대해 계산해
    # 프론트가 선택된 경로(selectedIdx)에 맞는 정보를 보여줄 수 있게 합니다.
    elevator_info_list = []
    if mode != 'general' and routes:
        for r in routes[:10]:
            elevator_info_list.append(get_elevator_tip_for_route(r))

    if rush_hour and routes:
        if mode == 'general':
            if GENERAL_ROUTE_AVAILABLE:
                # ⬅️ 일반인 모드: 실시간 여석 반영
                occupancy_data = get_bus_occupancy_for_route(routes[0].get("sub_paths", []))
                rush_hour_result = get_gemini_general_recommendation(
                    routes, occupancy_data, start, end, hour, minute, weekday
                )
            else:
                rush_hour_result = {
                    "recommended_index": 0,
                    "rush_hour_tip": "일반인 모드 기능(services.general_route)이 아직 준비되지 않았습니다.",
                    "alternative": "",
                }
        else:
            # 교통약자 모드: 기존 로직 + 엘리베이터 정보 + 노약자/임산부 여부 반영
            rush_hour_result = get_gemini_rush_hour_recommendation(
                routes, start, end, hour, minute, weekday,
                elevator_info=elevator_info_list[0] if elevator_info_list else None,
                accessibility_type=accessibility_type,
            )

    final_result["is_rush_hour"] = rush_hour
    final_result["rush_hour_result"] = rush_hour_result
    final_result["accessibility_type"] = accessibility_type
    # elevator_info: 하위 호환용 (첫 번째 경로 기준)
    final_result["elevator_info"] = elevator_info_list[0] if elevator_info_list else None
    # elevator_info_list: 경로별 엘리베이터 안내 (routes 배열과 동일한 순서/길이)
    final_result["elevator_info_list"] = elevator_info_list

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