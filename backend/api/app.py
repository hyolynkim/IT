"""
FastAPI 백엔드 — 이동약자 경로 추천 + 엘리베이터 인접 하차칸 안내
=================================================================

두 가지 기능을 하나의 웹 API로 제공합니다:
1. /api/guide            : subway_elevator_guide.py 로직 (기존)
2. /api/recommend-route  : accessible_route_recommender.py 로직 (신규)
                           Google Maps + Gemini + 하차칸 안내를 한 번에 묶어서 반환

data.go.kr / Gemini / Google Maps 서비스키는 모두 이 서버(백엔드) 환경변수로만
관리합니다. 브라우저(프론트엔드)에는 절대 키가 노출되지 않습니다.

설치
----
    pip install fastapi uvicorn requests

실행 전 환경변수 설정 (필수: GEMINI_API_KEY / 선택: GOOGLE_MAPS_API_KEY, SUBWAY_API_KEY)
----------------------------------------------------------------------------
    맥/리눅스:
        export GEMINI_API_KEY="..."
        export GOOGLE_MAPS_API_KEY="..."
        export SUBWAY_API_KEY="..."
    윈도우(PowerShell):
        $env:GEMINI_API_KEY="..."
        $env:GOOGLE_MAPS_API_KEY="..."
        $env:SUBWAY_API_KEY="..."

실행
----
    uvicorn app:app --reload --port 8000

브라우저에서 확인
----------------
    http://localhost:8000/api/guide?line=2호선&station=강남역
    http://localhost:8000/api/recommend-route?origin=서울역&destination=강남역
    http://localhost:8000/docs   (자동 생성되는 API 문서)
"""

import os
from dataclasses import asdict

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from subway_elevator_guide import (
    SERVICE_KEY,
    fetch_quick_get_off_info,
    list_covered_stations,
)
from accessible_route_recommender import (
    get_routes_from_google_maps,
    get_sample_routes,
    build_prompt,
    call_gemini,
    find_route_by_summary,
    get_subway_legs,
    get_elevator_friendly_car,
)

app = FastAPI(title="엘리베이터 인접 하차칸 안내 API")

# 개발 중엔 모든 출처를 허용하고, 실제 배포 시엔 프론트엔드 도메인만 넣어주세요.
# 예: allow_origins=["https://내프론트도메인.com"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/guide")
def get_guide(
    line: str = Query(..., description="호선 (예: 2호선)"),
    station: str = Query(..., description="역명 (예: 강남역)"),
):
    """노선/역명을 받아 엘리베이터(또는 대체 설비) 인접 하차칸 정보를 반환합니다."""
    if not station.endswith("역"):
        station += "역"

    if not SERVICE_KEY or SERVICE_KEY == "여기에_발급받은_서비스키를_입력하세요":
        return {
            "status": "error",
            "reason": "no_service_key",
            "message": "서버에 서비스키가 설정되지 않았어요. "
                       "SUBWAY_API_KEY 환경변수를 설정해주세요.",
        }

    info = fetch_quick_get_off_info(line, station)

    if info is None:
        return {
            "status": "error",
            "reason": "api_call_failed",
            "message": "공공데이터 API 호출에 실패했어요. 잠시 후 다시 시도해주세요.",
        }

    if not info.station_found:
        covered = list_covered_stations(line, SERVICE_KEY)
        return {
            "status": "not_found",
            "line": line,
            "station": station,
            "message": "해당 역 정보를 찾을 수 없어요. "
                       "철자를 확인하거나, 아직 데이터가 등록되지 않았을 수 있어요.",
            "covered_stations": covered or [],
        }

    if not info.directions:
        return {
            "status": "no_facility",
            "line": line,
            "station": station,
            "message": "이 역에는 엘리베이터·에스컬레이터 안내 정보가 등록되어 있지 않아요.",
        }

    return {
        "status": "ok",
        "line": info.line,
        "station": info.station,
        "directions": [asdict(d) for d in info.directions],
    }


@app.get("/api/stations")
def get_covered_stations(
    line: str = Query(..., description="호선 (예: 2호선)"),
    limit: int = Query(30, description="가져올 최대 역 개수"),
):
    """해당 노선에서 실제로 데이터가 등록된 역 목록을 반환합니다."""
    if not SERVICE_KEY or SERVICE_KEY == "여기에_발급받은_서비스키를_입력하세요":
        return {"status": "error", "reason": "no_service_key", "stations": []}

    covered = list_covered_stations(line, SERVICE_KEY, limit=limit)
    if covered is None:
        return {"status": "error", "reason": "api_call_failed", "stations": []}

    return {"status": "ok", "line": line, "stations": covered}


@app.get("/api/recommend-route")
def recommend_route(
    origin: str = Query(..., description="출발지 (예: 서울역)"),
    destination: str = Query(..., description="목적지 (예: 강남역)"),
):
    """
    임산부/노약자를 위한 카테고리별 경로 추천 + 지하철 구간의 하차칸 안내를
    한 번에 묶어서 반환합니다.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return {
            "status": "error",
            "reason": "no_gemini_key",
            "message": "서버에 GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.",
        }

    maps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    try:
        if maps_key:
            routes = get_routes_from_google_maps(origin, destination, maps_key)
        else:
            routes = get_sample_routes()
    except Exception as e:
        return {
            "status": "error",
            "reason": "maps_api_failed",
            "message": f"경로 조회 중 오류가 발생했습니다: {e}",
        }

    if not routes:
        return {
            "status": "no_routes",
            "message": "해당 출발지/목적지 간 경로를 찾을 수 없습니다.",
        }

    try:
        prompt = build_prompt(origin, destination, routes)
        result = call_gemini(prompt, gemini_key)
    except Exception as e:
        return {
            "status": "error",
            "reason": "gemini_api_failed",
            "message": f"Gemini 분석 중 오류가 발생했습니다: {e}",
        }

    # 카테고리별로 지하철 구간이 있으면 하차칸 안내를 붙여서 반환
    for cat in result.get("categories", []):
        matched_route = find_route_by_summary(routes, cat.get("route_summary", ""))
        cat["route_detail"] = matched_route  # 프론트엔드에서 거리/시간 등을 보여줄 때 사용

        subway_guides = []
        for line_name, station_name in get_subway_legs(matched_route):
            car_info = get_elevator_friendly_car(line_name, station_name)
            subway_guides.append(
                {
                    "line": line_name,
                    "station": station_name,
                    "status": car_info["status"],
                    "message": car_info["message"],
                }
            )
        cat["subway_guides"] = subway_guides

    return {
        "status": "ok",
        "origin": origin,
        "destination": destination,
        "routes": routes,
        "recommendation": result,
    }