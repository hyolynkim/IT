
"""Congestion · PY
CAT 프로젝트 - 버스 혼잡도(통계 기반) 계산 모듈
 
데이터 출처: 서울 열린데이터광장 - 서울시 버스노선별 정류장별 시간대별 승하차 인원 정보
(2025.10 ~ 2026.07, 10개월 평균, 월 합계를 각 달의 일수로 나눠 "하루 평균 승하차인원"으로 환산)
 
혼잡도 산정 방식: 노선 내 상대기준 (백분위 순위)
  - 절대 인원수(예: 회기역 하루 500명 vs 마을버스 정류장 1명)는 정류장 성격에 따라
    편차가 너무 커서 전체 공통 절대기준으로는 등급이 왜곡됨.
  - 단순 "최댓값 대비 비율"도 극단값(환승역 등) 하나가 스케일을 왜곡시켜
    나머지 정류장이 죄다 낮게 깔리는 문제가 있음.
  - 대신 그 노선, 그 시간대 내에서 각 정류장의 백분위 순위(percentile rank)를
    사용 -> 극단값에 영향을 덜 받고, 등급 분포가 항상 일정하게 유지됨.
 
사용법:
    from congestion import CongestionEstimator
 
    est = CongestionEstimator("bus_congestion_avg_wide.csv")
 
    # 노선 검색 -> 경유 정류소 전체 + 현재시각 혼잡도 (노선 내 상대비교)
    stops = est.get_route_stops("740", hour=8)
"""
 
import os
import pandas as pd
 
 
# bus_congestion.py 파일 위치(backend/api/) 기준으로 backend/data/ 경로를 자동 계산
# -> app.py를 어느 위치에서 실행하든(터미널 cwd와 무관) 항상 정확한 CSV 경로를 찾음
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV_PATH = os.path.join(_THIS_DIR, "..", "data", "bus_congestion_avg_wide.csv")
 
 
# ---- 노선 내 상대기준 threshold (그 노선 해당 시간대 내 백분위 순위) ----
THRESHOLD_LOW = 0.4    # 하위 40% 미만이면 "여유"
THRESHOLD_HIGH = 0.75  # 상위 25% (즉 순위 0.75 이상)면 "혼잡" (그 사이는 "보통")
 
 
def _hour_col(hour: int, kind: str) -> str:
    """0~23시 -> 실제 CSV 컬럼명으로 변환 ('00시'는 0시만 패딩, 나머지는 '1시'~'23시')"""
    if kind not in ("승차", "하차"):
        raise ValueError("kind must be '승차' or '하차'")
    h = "00" if hour == 0 else str(hour)
    return f"{h}시{kind}총승객수"
 
 
def get_congestion_level(percentile: float) -> dict:
    """
    (그 노선 해당 시간대 내) 백분위 순위 -> 혼잡도 등급 + bar 퍼센트
 
    Returns:
        {"level": "여유"|"보통"|"혼잡", "bar_percent": int (0~100)}
    """
    if percentile < THRESHOLD_LOW:
        level = "여유"
    elif percentile < THRESHOLD_HIGH:
        level = "보통"
    else:
        level = "혼잡"
 
    bar_percent = min(100, round(percentile * 100))
    return {"level": level, "bar_percent": bar_percent}
 
 
class CongestionEstimator:
    def __init__(self, csv_path: str = DEFAULT_CSV_PATH):
        self.df = pd.read_csv(
            csv_path,
            dtype={"노선번호": str, "버스정류장ARS번호": str, "표준버스정류장ID": "int64"},
        )
 
    def get_route_stops(self, route_number: str, hour: int, kind: str = "승차") -> list[dict]:
        """
        노선번호로 검색 -> 그 노선이 지나는 모든 정류소 + 지정 시간대 혼잡도
        (그 노선, 그 시간대 내에서의 백분위 순위 기반 상대적 혼잡도)
 
        Args:
            route_number: 노선번호 (예: "740")
            hour: 0~23
            kind: "승차" 또는 "하차" (기본 승차 기준으로 혼잡도 판단)
 
        Returns:
            정류소별 혼잡도 정보 리스트 (순서: 데이터 원본 순서, 노선 진행 순서 아님)
        """
        col = _hour_col(hour, kind)
        rows = self.df[self.df["노선번호"] == route_number].copy()
 
        if rows.empty:
            return []
 
        # 백분위 순위 계산 (동점은 평균 순위, 0~1 범위)
        rows["_pct"] = rows[col].rank(pct=True, method="average")
 
        results = []
        for _, row in rows.iterrows():
            congestion = get_congestion_level(row["_pct"])
            results.append({
                "노선번호": row["노선번호"],
                "노선명": row["노선명"],
                "정류장ID": int(row["표준버스정류장ID"]),
                "ARS번호": row["버스정류장ARS번호"],
                "역명": row["역명"],
                "hour": hour,
                "count": round(float(row[col]), 1),  # 하루 평균 승하차인원 (참고용)
                **congestion,
            })
        return results
 
    def get_congestion(self, route_number: str, stop_id: int, hour: int, kind: str = "승차") -> dict | None:
        """특정 노선 + 특정 정류소의 지정 시간대 혼잡도 단건 조회 (노선 내 상대비교)"""
        stops = self.get_route_stops(route_number, hour, kind)
        for s in stops:
            if s["정류장ID"] == stop_id:
                return s
        return None
 
 
if __name__ == "__main__":
    est = CongestionEstimator()  # 인자 없이 호출하면 backend/data/bus_congestion_avg_wide.csv 자동 탐색
    stops = est.get_route_stops("740", hour=8)
    print(f"740번 정류소 수: {len(stops)}")
    for s in sorted(stops, key=lambda x: -x["count"])[:5]:
        print(s)
 
    from collections import Counter
    levels = Counter(s["level"] for s in stops)
    print("등급 분포:", dict(levels))
 
 
