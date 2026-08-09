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