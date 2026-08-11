import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from api.gbis_service import get_bus_arrival  # 저번에 만든 GBIS 호출 함수 재사용

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

        result = {"level": level, "label": label, "source": "remainSeat"}
        # 혼잡 판정인데 아직 몇 정거장 전이면(=지금 정류소에 도착하기 전에 만석 될
        # 위험) 그냥 "혼잡"이라고만 알려주는 것보다 몇 정거장 전에서 미리 타라고
        # 알려주는 게 더 실질적인 대안임 — 호출하는 쪽(get_bus_occupancy_for_route)
        # 에서 이 플래그를 보고 실제 정류소 이름을 채워줌.
        if level == "혼잡" and location_no > 2:
            result["needs_earlier_boarding"] = True
        return result

    if crowded is not None:
        mapping = {1: "여유", 2: "보통", 3: "혼잡", 4: "매우혼잡"}
        level = mapping.get(crowded, "판단불가")
        return {"level": level, "label": f"차내혼잡도 {level}", "source": "crowded"}

    return {"level": "판단불가", "label": "혼잡도 정보 없음", "source": None}


from api.gbis_service import (
    get_bus_arrival,
    get_station_id_by_name,
    get_route_id_by_name,
    get_route_stations,
)

EARLIER_BOARDING_STOPS_BACK = 2  # "전전 정거장" = 2정거장 전


def _find_earlier_boarding_stop(route_name, station_id, stops_back=EARLIER_BOARDING_STOPS_BACK):
    """혼잡 위험이 있는 버스 구간에 대해, stops_back 정거장 전 정류소 이름을 찾음.
    노선의 전체 경유 정류소 목록에서 지금 타려는 정류소 위치를 찾아 그만큼 앞으로
    간 정류소를 돌려줌. 노선/정류소를 못 찾거나 이미 첫 정류소 근처면 None."""
    try:
        route_info = get_route_id_by_name(route_name)
        if not route_info or not route_info.get("routeId"):
            return None
        stations = get_route_stations(route_info["routeId"])
        if not stations:
            return None
        idx = next(
            (i for i, s in enumerate(stations) if str(s.get("stationId")) == str(station_id)),
            None,
        )
        if idx is None or idx - stops_back < 0:
            return None
        return stations[idx - stops_back].get("stationName")
    except Exception as e:
        print(f"[GBIS] 미리 타기 정류소 조회 실패: {e}", flush=True)
        return None


try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

_anthropic_client = None
CLAUDE_RUSH_HOUR_MODEL = "claude-haiku-4-5"

_RUSH_HOUR_TIP_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended_index": {"type": "integer"},
        "rush_hour_tip": {"type": "string"},
        "alternative": {"type": "string"},
    },
    "required": ["recommended_index", "rush_hour_tip", "alternative"],
    "additionalProperties": False,
}


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수에서 읽음
    return _anthropic_client


def get_gemini_general_recommendation(routes, occupancy_data, start, end, hour, minute, weekday):
    """이름은 예전 Gemini 시절 그대로 유지했지만(앱 코드에서 이 이름으로
    import함) 실제로는 Claude(Anthropic API)를 호출함 — Gemini는 무료
    할당량이 0으로 막혀 있어서(계정/결제 설정 문제) Claude로 교체."""
    if not _ANTHROPIC_AVAILABLE or not os.environ.get("ANTHROPIC_API_KEY"):
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

    prompt = f"""당신은 한국 수도권 대중교통 러시아워 전문가입니다.

출발지: {start} / 도착지: {end} / 시각: {hour}시 {minute}분
{occupancy_note}

recommended_index, rush_hour_tip(한국어, 2문장), alternative(한국어, 없으면 빈 문자열)를 채워 응답하세요."""

    try:
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
            crowding = classify_bus_crowding(matched_bus)
            if crowding.pop("needs_earlier_boarding", False):
                earlier_stop = _find_earlier_boarding_stop(route_name, station_id)
                if earlier_stop:
                    crowding["earlier_boarding_stop"] = earlier_stop
                    crowding["label"] = f"{crowding['label']} — {earlier_stop}에서 미리 타면 좋아요"
            leg["bus_congestion"] = cache[cache_key] = crowding
        else:
            leg["bus_congestion"] = cache[cache_key] = {"level": "판단불가", "label": "도착 예정 정보 없음"}

    return sub_paths