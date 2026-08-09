import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from api.gbis_service import get_bus_arrival  # 저번에 만든 GBIS 호출 함수 재사용

import requests
import json


def _to_int(value):
    """GBIS API는 숫자 필드도 문자열("", "12" 등)로 내려주는 경우가 많아
    비교 연산(>=) 전에 안전하게 int로 바꿔줌. 변환 불가하면 None."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_bus_crowding(bus: dict) -> dict:
    """경기도 광역버스는 전전 정거장부터 만석이 되는 경우가 많아
    여석 기준을 10석 이상으로 보수적으로 잡음."""
    remain_seat = _to_int(bus.get("remainSeat1"))
    location_no = _to_int(bus.get("locationNo1")) or 0
    crowded = _to_int(bus.get("crowded1"))

    if remain_seat is not None and remain_seat != -1:
        if remain_seat >= 10:
            level, label = "여유", f"빈자리 {remain_seat}석"
        elif remain_seat >= 5:
            if location_no <= 2:
                level, label = "보통", f"빈자리 {remain_seat}석, 곧 도착"
            else:
                level, label = "혼잡", f"빈자리 {remain_seat}석이지만 {location_no}정거장 전이라 도착 전 만석 가능"
        else:
            level, label = "혼잡", f"빈자리 {remain_seat}석"
        return {"level": level, "label": label, "source": "remainSeat"}

    if crowded is not None:
        mapping = {1: "여유", 2: "보통", 3: "혼잡", 4: "매우혼잡"}
        level = mapping.get(crowded, "판단불가")
        return {"level": level, "label": f"차내혼잡도 {level}", "source": "crowded"}

    return {"level": "판단불가", "label": "혼잡도 정보 없음", "source": None}


from api.gbis_service import get_bus_arrival, get_station_id_by_name

def get_bus_occupancy_for_route(sub_paths):
    """경로의 각 버스 구간(첫 정류소 기준)에 대해 GBIS 여석 정보를 조회."""
    bus_legs = [s for s in sub_paths if s.get("traffic_type") == 2]
    occupancy_list = []

    for leg in bus_legs:
        station_name = leg.get("start_name")
        route_name = leg.get("lane_name")  # 버스 번호
        lat = leg.get("start_lat")
        lng = leg.get("start_lng")

        if not station_name or not route_name:
            continue

        station_id = get_station_id_by_name(station_name, lat, lng)
        if not station_id:
            continue

        arrival_data = get_bus_arrival(station_id)
        if "error" in arrival_data:
            continue

        matched_bus = next(
            (b for b in arrival_data.get("buses", []) if b.get("routeName") == route_name),
            None
        )
        if matched_bus:
            crowding = classify_bus_crowding(matched_bus)
            occupancy_list.append({
                "routeName": route_name,
                "station": station_name,
                **matched_bus,
                **crowding,
            })

    return occupancy_list


def get_gemini_general_recommendation(routes, occupancy_data, start, end, hour, minute, weekday):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    if not GEMINI_API_KEY:
        return {
            "recommended_index": 0,
            "rush_hour_tip": "API 키 설정 후 러시아워 분석이 제공됩니다.",
            "alternative": ""
        }

    occupancy_note = ""
    if occupancy_data:
        lines = [f"- {o['routeName']} ({o['station']}): {o['label']}" for o in occupancy_data]
        occupancy_note = (
            "\n실시간 버스 여석 정보:\n" + "\n".join(lines) +
            "\n혼잡한 버스라면 다음 정거장 탑승이나 대안을 rush_hour_tip에 반영해주세요."
        )

    prompt = f"""
당신은 한국 수도권 대중교통 러시아워 전문가입니다.

출발지: {start} / 도착지: {end} / 시각: {hour}시 {minute}분
{occupancy_note}

반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이):
{{
  "recommended_index": 0,
  "rush_hour_tip": "구체적인 러시아워 팁 (한국어, 2문장)",
  "alternative": "대안 제안 (한국어, 없으면 빈 문자열)"
}}
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.3, "maxOutputTokens": 500}}

    try:
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        text = text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        return {"recommended_index": 0, "rush_hour_tip": f"분석 중 오류: {e}", "alternative": ""}
def get_bus_occupancy_for_route(sub_paths, cache=None):
    """경로의 각 버스 구간(첫 정류소 기준)에 GBIS 여석 정보를 조회해
    sub_paths의 bus_congestion 필드를 직접 채운다 (in-place).

    cache: {(station_name, route_name): bus_congestion} 딕셔너리를 넘기면
    같은 (정류소, 버스번호) 조합을 여러 경로가 공유할 때 GBIS를 중복
    호출하지 않는다. 후보 경로가 많을 때(app.py에서 여러 경로에 대해
    이 함수를 반복 호출) 응답 시간이 크게 늘어나는 걸 막기 위함 —
    캐시 없이는 후보 경로 수 × 버스 구간 수만큼 순차 호출이 쌓여
    타임아웃(500)으로 이어졌음.
    """
    if cache is None:
        cache = {}

    for leg in sub_paths:
        if leg.get("traffic_type") != 2:
            continue

        station_name = leg.get("start_name")
        route_name = leg.get("lane_name")
        lat = leg.get("start_lat")
        lng = leg.get("start_lng")

        if not station_name or not route_name:
            leg["bus_congestion"] = {"level": "판단불가", "label": "정보 없음"}
            continue

        cache_key = (station_name, route_name)
        if cache_key in cache:
            leg["bus_congestion"] = cache[cache_key]
            continue

        station_id = get_station_id_by_name(station_name, lat, lng)
        if not station_id:
            leg["bus_congestion"] = cache[cache_key] = {"level": "판단불가", "label": "정류소 정보 없음"}
            continue

        arrival_data = get_bus_arrival(station_id)
        if "error" in arrival_data:
            leg["bus_congestion"] = cache[cache_key] = {"level": "판단불가", "label": "조회 실패"}
            continue

        matched_bus = next(
            (b for b in arrival_data.get("buses", []) if str(b.get("routeName")) == str(route_name)),
            None
        )
        if matched_bus:
            leg["bus_congestion"] = cache[cache_key] = classify_bus_crowding(matched_bus)
        else:
            leg["bus_congestion"] = cache[cache_key] = {"level": "판단불가", "label": "도착 예정 정보 없음"}

    return sub_paths