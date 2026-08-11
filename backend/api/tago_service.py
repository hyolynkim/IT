import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

SERVICE_KEY = "c1UVTmspAy1/9y1h9T41mvJEUqQ5265VeC79svxBTHqBP1RBZTDXbuXGR4yHeJY8q+DgLKT8oq2ROnMzxO6d/g=="

ROUTE_URL = "https://apis.data.go.kr/1613000/BusRouteInfoInqireService"
ARRIVAL_URL = "https://apis.data.go.kr/1613000/ArvlInfoInqireService"

# 검색 대상 도시 (혼잡/교통량 상위 지역 9곳)
TARGET_CITIES = {
    "31020": "성남시",
    "31010": "수원시",
    "31190": "용인시",
    "31100": "고양시",
    "31050": "부천시",
    "31040": "안양시",
    "21": "부산광역시",
    "23": "인천광역시",
    "22": "대구광역시",
}


def _search_route_in_city(route_nm: str, city_code: str):
    """한 도시에서 노선을 검색. 없으면 None 반환."""
    url = f"{ROUTE_URL}/getRouteNoList"
    params = {
        "serviceKey": SERVICE_KEY,
        "cityCode": city_code,
        "routeNo": route_nm,
        "numOfRows": 20,
        "pageNo": 1,
        "_type": "json",
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG] TAGO 노선검색 실패 (city={city_code}): {e}", flush=True)
        return None

    body = data.get("response", {}).get("body", {})
    items = body.get("items", {})
    if not items:
        return None
    item_list = items.get("item", [])
    if isinstance(item_list, dict):
        item_list = [item_list]
    if not item_list:
        return None

    exact = [it for it in item_list if str(it.get("routeno")) == route_nm]
    chosen = exact[0] if exact else item_list[0]
    return {
        "routeId": chosen.get("routeid"),
        "routeNo": chosen.get("routeno"),
        "routeType": chosen.get("routetp"),
        "startNodeNm": chosen.get("startnodenm"),
        "endNodeNm": chosen.get("endnodenm"),
        "cityCode": city_code,
        "cityName": TARGET_CITIES.get(city_code, city_code),
    }


def get_route_id_by_name(route_nm: str, city_code: str = None):
    """city_code가 주어지면 그 도시만 검색, 없으면 9개 도시 병렬 검색."""
    if city_code:
        result = _search_route_in_city(route_nm, city_code)
        return result

    with ThreadPoolExecutor(max_workers=len(TARGET_CITIES)) as executor:
        futures = {
            executor.submit(_search_route_in_city, route_nm, code): code
            for code in TARGET_CITIES
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                return result
    return None

def get_route_stations(route_id: str, city_code: str):
    url = f"{ROUTE_URL}/getRouteAcctoThrghSttnList"
    params = {
        "serviceKey": SERVICE_KEY,
        "cityCode": city_code,
        "routeId": route_id,
        "numOfRows": 200,
        "pageNo": 1,
        "_type": "json",
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG] TAGO 경유정류소 요청 실패: {e}", flush=True)
        return []

    body = data.get("response", {}).get("body", {})
    items = body.get("items", {})
    if not items:
        return []
    item_list = items.get("item", [])
    if isinstance(item_list, dict):
        item_list = [item_list]

    stations = [
        {
            "nodeId": it.get("nodeid"),
            "nodeName": it.get("nodenm"),
            "nodeOrd": it.get("nodeord"),
            "updownCd": it.get("updowncd"),
        }
        for it in item_list
    ]
    stations.sort(key=lambda s: int(s["nodeOrd"] or 0))
    return stations


def get_station_arrival(node_id: str, route_id: str, city_code: str):
    url = f"{ARRIVAL_URL}/getSttnAcctoSpcifyRouteBusArvlPrearngeInfoList"
    params = {
        "serviceKey": SERVICE_KEY,
        "cityCode": city_code,
        "nodeId": node_id,
        "routeId": route_id,
        "numOfRows": 10,
        "pageNo": 1,
        "_type": "json",
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
    except requests.exceptions.RequestException as e:
        print(f"[DEBUG] TAGO 도착정보 요청 실패: {e}", flush=True)
        return []

    body = data.get("response", {}).get("body", {})
    items = body.get("items", {})
    if not items:
        return []
    item_list = items.get("item", [])
    if isinstance(item_list, dict):
        item_list = [item_list]

    return [
        {
            "routeNo": it.get("routeno"),
            "arrTime": it.get("arrtime"),
            "arrPrevStationCnt": it.get("arrprevstationcnt"),
            "vehicleTp": it.get("vehicletp"),
        }
        for it in item_list
    ]


def _estimate_congestion(arr_prev_station_cnt):
    try:
        cnt = int(arr_prev_station_cnt)
    except (TypeError, ValueError):
        return "0", "정보없음"

    if cnt <= 2:
        return "5", "혼잡"
    elif cnt <= 5:
        return "4", "보통"
    else:
        return "3", "여유"


def get_route_congestion(route_nm: str, city_code: str = None, max_stations: int = 15):
    route_info = get_route_id_by_name(route_nm, city_code)
    if not route_info:
        msg = "해당 노선을 찾을 수 없습니다." if city_code else \
              "해당 노선을 찾을 수 없습니다. (성남/수원/용인/고양/부천/안양/부산/인천/대구 지역만 검색 가능)"
        return {"error": msg, "stations": []}
    
    route_id = route_info["routeId"]
    city_code = route_info["cityCode"]

    stations = get_route_stations(route_id, city_code)
    if not stations:
        return {"error": "경유 정류소 정보를 찾을 수 없습니다.", "stations": []}

    stations = stations[:max_stations]

    results = []
    for st in stations:
        node_id = st.get("nodeId")
        if not node_id:
            continue
        arrivals = get_station_arrival(node_id, route_id, city_code)
        matched = next((a for a in arrivals if str(a.get("routeNo")) == str(route_info["routeNo"])), None)

        if matched:
            level, label = _estimate_congestion(matched.get("arrPrevStationCnt"))
            arr_time = matched.get("arrTime")
        else:
            level, label = None, "정보없음"
            arr_time = None

        results.append({
            "stationName": st.get("nodeName"),
            "nodeOrd": st.get("nodeOrd"),
            "congestionLevel": level,
            "congestionLabel": label,
            "arrTimeSec": arr_time,
        })

    return {
        "routeId": route_id,
        "routeNo": route_info["routeNo"],
        "cityName": route_info["cityName"],
        "stations": results,
    }