"""
FastAPI 백엔드 — 엘리베이터 인접 하차칸 안내
=================================================

subway_elevator_guide.py 의 로직을 웹 API로 감싼 서버입니다.
프론트엔드(브라우저)는 이 서버의 /api/guide 만 호출하고,
data.go.kr 서비스키는 이 서버 안에만 존재합니다 (브라우저에 절대 노출되지 않음).

설치
----
    pip install fastapi uvicorn requests

실행
----
    uvicorn app:app --reload --port 8000

브라우저에서 확인
----------------
    http://localhost:8000/api/guide?line=2호선&station=강남역
    http://localhost:8000/docs   (자동 생성되는 API 문서)
"""

from dataclasses import asdict

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from subway_elevator_guide import (
    SERVICE_KEY,
    fetch_quick_get_off_info,
    list_covered_stations,
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