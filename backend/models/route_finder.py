import requests
import urllib.parse

ODSAY_API_KEY = "MRru/5qfTWVBfehL8LUgxA"
KAKAO_REST_API_KEY = "6c220101133197233daf87a3ec931801"

def get_coords_from_keyword(keyword):
    """카카오 로컬 API 좌표 변환"""
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {"query": keyword}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=3.0)
        if response.status_code == 200:
            documents = response.json().get('documents')
            if documents and len(documents) > 0:
                lon = str(documents[0]['x'])
                lat = str(documents[0]['y'])
                return lon, lat
    except Exception as e:
        print(f"카카오 API 에러: {e}")
        
    return None, None

# ================================
# 서울-경기권 광역버스 판별 함수
# ================================
def is_express_bus(bus_no: str) -> bool:
    bus_no = str(bus_no).strip()

    # M버스 (광역급행버스)
    if bus_no.startswith("M"):
        return True

    # G버스 (경기도 버스)
    if bus_no.startswith("G"):
        return True

    # 서울 광역버스 (9xxx)
    if bus_no.startswith("9") and len(bus_no) == 4:
        return True

    # 경기 광역버스 번호 목록
    express_prefixes = [
        # 1000번대
        "1000", "1001", "1002", "1003", "1004", "1005", "1006",
        "1007", "1008", "1009", "1010", "1011", "1030",
        # 2000번대
        "2000", "2001", "2002", "2003",
        # 3000번대
        "3000", "3001", "3002", "3003", "3007", "3100",
        # 5000번대 (광역버스 핵심)
        "5000", "5001", "5002", "5003", "5004", "5005",
        "5500", "5600", "5700",
        # 6000번대
        "6000", "6001", "6002", "6003", "6004", "6005",
        "6006", "6007", "6008", "6009", "6010",
        # 7000번대
        "7000", "7001", "7002", "7003", "7004", "7005",
        "7006", "7007", "7770",
        # 8000번대
        "8001", "8002", "8003", "8004", "8005",
        "8100", "8109", "8110",
    ]

    for prefix in express_prefixes:
        if bus_no.startswith(prefix):
            return True

    return False

def extract_sub_paths(path):
    """ODsay 원본 데이터에서 상세 이동 수단 추출"""
    sub_paths = []
    for sub_path in path.get("subPath", []):
        traffic_type = sub_path.get("trafficType")

        # ODsay: lane은 객체({}) 또는 리스트([{}]) 둘 다 올 수 있음
        raw_lane = sub_path.get("lane", {})
        if isinstance(raw_lane, list):
            lane = raw_lane[0] if raw_lane else {}
        else:
            lane = raw_lane

        # passStopList 정류장 목록 (x=경도, y=위도)
        stations_raw = sub_path.get("passStopList", {}).get("stations", [])
        pass_stop_list = [
            {
                "name": s.get("stationName", ""),
                "lat": s.get("y"),   # ODsay y = 위도
                "lng": s.get("x"),   # ODsay x = 경도
            }
            for s in stations_raw
            if s.get("x") and s.get("y")
        ]

        # 공통 좌표 (ODsay: startX=경도, startY=위도)
        coords = {
            "start_lat": sub_path.get("startY"),
            "start_lng": sub_path.get("startX"),
            "end_lat":   sub_path.get("endY"),
            "end_lng":   sub_path.get("endX"),
        }

        if traffic_type == 1:  # 지하철
            sub_paths.append({
                "traffic_type": 1,
                "type_name": "지하철",
                "lane_name": lane.get("name", ""),
                "start_name": sub_path.get("startName", ""),
                "end_name": sub_path.get("endName", ""),
                **coords,
                "pass_stop_list": pass_stop_list,
                "station_count": sub_path.get("stationCount", 0),
                "section_time_min": sub_path.get("sectionTime", 0),
                "is_express": False,
                "stations": [s.get("stationName") for s in stations_raw],
            })

        elif traffic_type == 2:  # 버스
            bus_no = lane.get("busNo", "")
            express = is_express_bus(bus_no)
            sub_paths.append({
                "traffic_type": 2,
                "type_name": "버스",
                "lane_name": bus_no,
                "start_name": sub_path.get("startName", ""),
                "end_name": sub_path.get("endName", ""),
                **coords,
                "pass_stop_list": pass_stop_list,
                "station_count": sub_path.get("stationCount", 0),
                "section_time_min": sub_path.get("sectionTime", 0),
                "is_express": express,
                "stations": [s.get("stationName") for s in stations_raw],
                "bus_congestion": None,
            })

        elif traffic_type == 3:  # 도보
            sub_paths.append({
                "traffic_type": 3,
                "type_name": "도보",
                "section_time_min": sub_path.get("sectionTime", 0),
                **coords,
            })

    return sub_paths

def get_odsay_route(sx, sy, ex, ey):
    """ODsay API 호출"""
    safe_key = urllib.parse.unquote(ODSAY_API_KEY)
    url = "https://api.odsay.com/v1/api/searchPubTransPathT"
    params = {"apiKey": safe_key, "SX": sx, "SY": sy, "EX": ex, "EY": ey, "SearchPathType": 0}
    
    try:
        headers = {"Origin": "http://localhost:8000", "Referer": "http://localhost:8000/"}
        response = requests.get(url, params=params, headers=headers, timeout=5.0)
        if response.status_code == 200:
            return response.json()
    except:
        return None

def find_cat_optimal_route(start_name, end_name, departure_hour, mode="general"):
    """최적 경로 반환 메인 로직

    mode:
      - "general" (기본값): 기존과 동일 — 광역버스 포함 경로 우선, 그다음 소요시간 순
      - "accessibility": 노약자/임산부용 — 환승 횟수 · 도보 시간이 적은(=몸에 부담이 적은)
        경로를 우선으로 재정렬 (같은 후보 경로 목록 안에서 순서만 다르게)
    """
    sx, sy = get_coords_from_keyword(start_name)
    ex, ey = get_coords_from_keyword(end_name)
    
    if not sx or not ex:
        return {
            "status": "fail",
            "message": f"출발지('{start_name}') 또는 목적지('{end_name}')의 위치를 찾을 수 없습니다."
        }
        
    odsay_data = get_odsay_route(sx, sy, ex, ey)
    if not odsay_data or "result" not in odsay_data or "path" not in odsay_data["result"]:
        return {"status": "fail", "message": "대중교통 경로를 찾을 수 없습니다."}
    
    path_list = odsay_data["result"]["path"]
    refined_paths = []
    
    for path in path_list:
        base_time = path["info"]["totalTime"]
        transfer_count = path["info"].get("transferCount", 0)
        
        detailed_segments = extract_sub_paths(path)
        has_express_bus = any(
            seg.get("is_express", False)
            for seg in detailed_segments
            if seg["traffic_type"] == 2
        )

        # 도보 구간 총 소요시간 (노약자 모드 정렬에 사용 — 도보가 적을수록 부담이 적음)
        walk_time_total_min = sum(
            seg.get("section_time_min", 0)
            for seg in detailed_segments
            if seg["traffic_type"] == 3
        )
        
        total_penalty = 0
        if has_express_bus and (8 <= departure_hour <= 10 or 18 <= departure_hour <= 20):
            total_penalty += 30
            
        comfort_time = base_time + total_penalty
        
        refined_paths.append({
            "path_type": path["pathType"],
            "original_time_min": base_time,
            "estimated_comfort_time_min": comfort_time,
            "payment_krw": path["info"].get("payment", 0),
            "transfer_count": transfer_count,
            "walk_time_total_min": walk_time_total_min,
            "has_express_bus": has_express_bus,
            "first_start_station": path["info"].get("firstStartStation", ""),
            "last_end_station": path["info"].get("lastEndStation", ""),
            "sub_paths": detailed_segments
        })

    if mode == "accessibility":
        # ✅ 노약자/임산부 모드: 환승이 적고, 그다음 도보가 적은 경로를 우선
        # (같은 후보군 안에서 "빠른 길"이 아니라 "부담이 적은 길"을 기준으로 재정렬)
        refined_paths.sort(key=lambda x: (
            x["transfer_count"],
            x["walk_time_total_min"],
            x["estimated_comfort_time_min"],
        ))
    else:
        # ✅ 일반 모드(기존과 동일): 광역버스 포함 경로를 상위로 정렬 후 소요시간 순 정렬
        refined_paths.sort(key=lambda x: (
            not x["has_express_bus"],  # 광역버스 있는 경로 먼저
            x["estimated_comfort_time_min"]
        ))
    
    return {
        "status": "success",
        "start": start_name,
        "end": end_name,
        "departure_hour": departure_hour,
        "routes": refined_paths
    }
