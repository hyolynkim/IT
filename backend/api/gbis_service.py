import requests
import math

GBIS_BASE_URL = "https://apis.data.go.kr/6410000/busarrivalservice/v2/getBusArrivalListv2"
SERVICE_KEY = "cd61ccd07dacf5c797fcec0a62e10ecd087d7a0ad10a6ad3bc45c8294d4ec0f3"  # .env로 분리 권장

# 여석 정보가 제공되는 노선 유형 코드
SEAT_INFO_ROUTE_TYPES = {11, 12, 14, 16, 17, 21, 22}

def get_station_id_by_name(station_name: str, target_lat: float = None, target_lng: float = None):
    url = "https://apis.data.go.kr/6410000/busstationservice/v2/getBusStationListv2"
    params = {
        "serviceKey": SERVICE_KEY,
        "keyword": station_name,
        "format": "json",
    }

    try:
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()
        print(f"[DEBUG] GBIS 정류소 검색 응답: {data}", flush=True)
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG] GBIS 요청 자체 실패: {e}", flush=True)
        return None

    # ⬅️ 수정: response > msgBody > busStationList 경로로 파싱
    station_list = data.get("response", {}).get("msgBody", {}).get("busStationList", [])
    if isinstance(station_list, dict):
        station_list = [station_list]
    if not station_list:
        return None

    if target_lat is None or target_lng is None:
        return station_list[0].get("stationId")

    def distance(s):
        try:
            dy = float(s.get("y", 0)) - target_lat
            dx = float(s.get("x", 0)) - target_lng
            return dy * dy + dx * dx
        except (TypeError, ValueError):
            return float("inf")

    closest = min(station_list, key=distance)
    return closest.get("stationId")


def get_bus_arrival(station_id: str):
    """특정 정류소의 도착 예정 버스 목록 + 여석 정보를 반환"""
    params = {
        "serviceKey": SERVICE_KEY,
        "stationId": station_id,
        "format": "json",
    }

    try:
        res = requests.get(GBIS_BASE_URL, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()
        print(f"[DEBUG] GBIS 도착정보 응답: {data}", flush=True)  # 확인용, 나중에 지워도 됨
    except requests.exceptions.RequestException as e:
        return {"error": f"API 호출 실패: {e}"}

    # ⬅️ 수정: response > msgHeader / msgBody 경로로 파싱
    response = data.get("response", {})
    msg_header = response.get("msgHeader", {})
    msg_body = response.get("msgBody", {})

    result_code = msg_header.get("resultCode")
    if result_code == 4:
        return {"buses": [], "message": "도착 예정 버스 없음"}
    if result_code != 0:
        return {"error": msg_header.get("resultMessage", "알 수 없는 오류")}

    raw_list = msg_body.get("busArrivalList", [])
    if isinstance(raw_list, dict):
        raw_list = [raw_list]

    buses = []
    for item in raw_list:
        route_type = item.get("routeTypeCd")
        has_seat_info = route_type in SEAT_INFO_ROUTE_TYPES

        buses.append({
            "routeId": item.get("routeId"),
            "routeName": item.get("routeName"),
            "routeTypeCd": route_type,
            "predictTime1": item.get("predictTime1"),
            "predictTime2": item.get("predictTime2"),
            "remainSeat1": item.get("remainSeatCnt1") if has_seat_info else None,
            "remainSeat2": item.get("remainSeatCnt2") if has_seat_info else None,
            "hasSeatInfo": has_seat_info,
        })

    return {"buses": buses}

# ── gbis_service.py에 추가 ──────────────────────────────────

def get_route_id_by_name(route_nm: str):
    """버스 번호(노선명)로 GBIS routeId를 찾습니다."""
    url = "https://apis.data.go.kr/6410000/busrouteservice/v2/getBusRouteListv2"
    params = {
        "serviceKey": SERVICE_KEY,
        "keyword": route_nm,
        "format": "json",
    }

    try:
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()
        print(f"[DEBUG] GBIS 노선 검색 응답: {data}", flush=True)
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG] GBIS 노선 검색 요청 실패: {e}", flush=True)
        return None

    route_list = data.get("response", {}).get("msgBody", {}).get("busRouteList", [])
    if isinstance(route_list, dict):
        route_list = [route_list]
    if not route_list:
        return None

    # 노선명이 정확히 일치하는 것 우선, 없으면 첫 번째 결과
    exact = [r for r in route_list if r.get("routeName") == route_nm]
    chosen = exact[0] if exact else route_list[0]
    return {
        "routeId": chosen.get("routeId"),
        "routeName": chosen.get("routeName"),
        "routeTypeCd": chosen.get("routeTypeCd"),
    }


def get_route_stations(route_id: str):
    """노선ID로 그 노선이 지나는 정류소 목록(순서 포함)을 조회합니다."""
    url = "https://apis.data.go.kr/6410000/busrouteservice/v2/getBusRouteStationListv2"
    params = {
        "serviceKey": SERVICE_KEY,
        "routeId": route_id,
        "format": "json",
    }

    try:
        res = requests.get(url, params=params, timeout=5)
        res.raise_for_status()
        data = res.json()
        print(f"[DEBUG] GBIS 경유정류소 응답: {data}", flush=True)
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG] GBIS 경유정류소 요청 실패: {e}", flush=True)
        return []

    station_list = data.get("response", {}).get("msgBody", {}).get("busRouteStationList", [])
    if isinstance(station_list, dict):
        station_list = [station_list]

    return [
        {
            "stationId": s.get("stationId"),
            "stationName": s.get("stationName"),
            "stationSeq": s.get("stationSeq"),
            "turnYn": s.get("turnYn"),  # 회차지 여부 (상행/하행 구분에 활용 가능)
        }
        for s in station_list
    ]


def get_route_congestion(route_nm: str, max_stations: int = None):
    """버스 번호로 검색해서, 그 노선이 지나는 모든 정류소의 혼잡도/여석 정보를 반환합니다.

    - route_nm: 검색할 버스 번호(노선명)
    - max_stations: 조회할 최대 정류소 수 (None이면 전체, 정류소가 많은 광역노선은
      순차 호출 시간이 길어지므로 필요시 제한 가능)
    """
    route_info = get_route_id_by_name(route_nm)
    if not route_info or not route_info.get("routeId"):
        return {"error": "해당 노선을 찾을 수 없습니다.", "stations": []}

    route_id = route_info["routeId"]
    stations = get_route_stations(route_id)
    if not stations:
        return {"error": "경유 정류소 정보를 찾을 수 없습니다.", "stations": []}

    if max_stations:
        stations = stations[:max_stations]

    results = []
    for st in stations:
        station_id = st.get("stationId")
        if not station_id:
            continue

        arrival = get_bus_arrival(station_id)
        if arrival.get("error"):
            results.append({
                "stationName": st.get("stationName"),
                "stationSeq": st.get("stationSeq"),
                "congestionLevel": None,
                "congestionLabel": "정보없음",
                "error": arrival["error"],
            })
            continue

        # 이 노선에 해당하는 버스 항목만 골라서 여석/혼잡도 추출
        matched = next(
            (b for b in arrival.get("buses", []) if b.get("routeId") == route_id),
            None
        )

        if matched and matched.get("hasSeatInfo"):
            seat = matched.get("remainSeat1")
            level, label = _seat_to_congestion(seat)
        else:
            level, label = None, "정보없음"

        results.append({
            "stationName": st.get("stationName"),
            "stationSeq": st.get("stationSeq"),
            "congestionLevel": level,
            "congestionLabel": label,
            "predictTime1": matched.get("predictTime1") if matched else None,
        })

    return {
        "routeId": route_id,
        "routeName": route_info.get("routeName"),
        "stations": results,
    }


def _seat_to_congestion(remain_seat):
    """여석 수를 혼잡도 라벨로 변환 (기준은 임시 — 팀 합의된 기준으로 조정 필요)"""
    if remain_seat is None:
        return None, "정보없음"
    try:
        n = int(remain_seat)
    except (TypeError, ValueError):
        return None, "정보없음"

    if n <= 0:
        return "5", "혼잡"
    elif n <= 5:
        return "4", "보통"
    else:
        return "3", "여유"