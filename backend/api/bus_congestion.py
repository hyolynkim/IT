import os
import csv

BUS_RIDERSHIP_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bus_ridership.csv")
_bus_ridership_cache = None

# 탑승 인원 수 기준 혼잡도 등급 임계값 (이 데이터셋의 러시아워 시간대 분포를
# 참고해서 정한 근사치 기준입니다 — 실제 버스 정원과는 무관합니다)
_LOW_THRESHOLD = 150
_HIGH_THRESHOLD = 500


def _load_bus_ridership():
    global _bus_ridership_cache
    if _bus_ridership_cache is not None:
        return _bus_ridership_cache

    data = {}
    if os.path.exists(BUS_RIDERSHIP_CSV_PATH):
        # 서울시 CSV는 내려받는 방식에 따라 CP949(EUC-KR 계열) 또는 UTF-8(-SIG)로
        # 올 수 있어서, 둘 다 순서대로 시도합니다.
        rows = None
        last_error = None
        for encoding in ("utf-8-sig", "cp949"):
            try:
                with open(BUS_RIDERSHIP_CSV_PATH, encoding=encoding) as f:
                    rows = list(csv.DictReader(f))
                break
            except (UnicodeDecodeError, UnicodeError) as e:
                last_error = e
                continue
            except Exception as e:
                last_error = e
                break

        if rows is None:
            print(f"[안내] 버스 승하차 인원 CSV 로딩 실패: {last_error}")
        else:
            for row in rows:
                route_no = (row.get("노선번호") or "").strip()
                raw_station = (row.get("역명") or "").strip()
                # "종로2가사거리(00089)"처럼 뒤에 ARS번호가 괄호로 붙어있어서 떼어냅니다.
                station = raw_station.split("(")[0].strip()
                key = (route_no, station)
                data.setdefault(key, []).append(row)

    _bus_ridership_cache = data
    return data


def preload_bus_ridership():
    """서버가 켜지는 시점에 미리 호출해서, 첫 검색 요청이 CSV 로딩(파일이 크면
    수십 초 걸릴 수 있음) 때문에 느려지지 않도록 캐시를 미리 채워둡니다.
    이미 캐시돼 있으면(=이미 한 번 불렀으면) 아무 일도 안 하고 바로 끝나요."""
    import time
    t0 = time.time()
    print("[안내] 버스 승하차 인원 데이터 미리 불러오는 중... (파일이 커서 시간이 좀 걸려요)")
    data = _load_bus_ridership()
    elapsed = time.time() - t0
    if data:
        print(f"[안내] 버스 승하차 인원 데이터 로딩 완료 ({len(data)}개 노선·정류장 조합, {elapsed:.1f}초)")
    else:
        print(f"[안내] 버스 승하차 인원 데이터를 찾지 못했어요 ({elapsed:.1f}초) — bus_ridership.csv 위치를 확인해주세요.")


def _boarding_column(hour):
    h = hour % 24
    if h == 0:
        return "00시승차총승객수"
    return f"{h}시승차총승객수"


def _alighting_column(hour):
    h = hour % 24
    if h == 0:
        return "00시하차총승객수"
    return f"{h}시하차총승객수"


def _congestion_label(boarding_count):
    if boarding_count < _LOW_THRESHOLD:
        return "여유"
    if boarding_count < _HIGH_THRESHOLD:
        return "보통"
    return "혼잡"


def is_bus_operating_at(route_no, station, hour):
    """해당 버스 노선이 그 정류장·시간대에 실제로 운행하는지 확인합니다.

    1) "N"으로 시작하는 노선(N37, N31 등)은 전부 심야버스입니다. 심야
       시간대(23시~04시)가 아니면, 데이터를 볼 것도 없이 바로 "운행 안 함"으로
       판단합니다 (심야버스는 낮에 절대 운행하지 않으니까요).
    2) 그 외 노선은, 승차·하차 인원이 등록된 모든 행에서 전부 0이면
       "운행 안 함"으로 판단합니다.

    데이터 자체가 없거나 유효한 숫자를 하나도 못 찾으면, 판단할 근거가 없는
    거라 True(운행한다고 가정)를 반환해서 잘못 걸러내는 걸 방지합니다."""
    NIGHT_BUS_HOURS = {23, 0, 1, 2, 3, 4}
    if route_no.strip().upper().startswith("N") and (hour % 24) not in NIGHT_BUS_HOURS:
        return False

    data = _load_bus_ridership()
    if not data:
        return True

    rows = data.get((route_no, station))
    if not rows:
        return True

    board_col = _boarding_column(hour)
    alight_col = _alighting_column(hour)

    found_valid_data = False
    for row in rows:
        raw_board = row.get(board_col)
        raw_alight = row.get(alight_col)
        if raw_board in (None, "") or raw_alight in (None, ""):
            continue
        try:
            board = int(raw_board)
            alight = int(raw_alight)
        except ValueError:
            continue
        found_valid_data = True
        if board > 0 or alight > 0:
            return True  # 하나라도 승하차 흔적이 있으면 운행 중인 것으로 판단

    if not found_valid_data:
        return True  # 판단할 데이터가 없음 — 걸러내지 않음

    return False  # 유효한 데이터가 있었는데 전부 0이었음 = 그 시간대엔 운행 안 함


def route_has_non_operating_bus(sub_paths, hour, minute):
    """경로 안에, 실제로 도달하는 시각 기준으로 운행하지 않는 버스 구간이
    하나라도 있으면 True를 반환합니다. (검색 시각 + 그 전까지의 이동 시간을
    누적해서, 그 버스 구간에 실제로 도달하는 시각을 기준으로 판단합니다.)"""
    elapsed = 0
    base_total_min = hour * 60 + minute
    for seg in sub_paths:
        if seg.get("traffic_type") == 2:
            leg_total_min = (base_total_min + elapsed) % (24 * 60)
            leg_hour = leg_total_min // 60

            route_no = (seg.get("lane_name") or "").strip()
            station = (seg.get("start_name") or "").strip()
            if route_no and station:
                if not is_bus_operating_at(route_no, station, leg_hour):
                    return True

        elapsed += seg.get("section_time_min", 0)

    return False


def get_bus_occupancy_for_route(sub_paths, hour=9, minute=0):
    """경로의 버스 구간들에 대해, 그 시간대 평균 탑승 인원 기반 혼잡도를 반환합니다.
    같은 (노선, 정류장)으로 등록된 행이 여러 개면 평균을 씁니다.
    검색 시각을 모든 구간에 그대로 쓰지 않고, 그 구간 앞에 있는 도보·지하철·
    버스 구간들의 소요시간을 누적해서 "실제로 그 구간에 도달하는 시각"을 구해
    사용합니다 — 환승 후 한참 뒤에 타는 버스라면 검색 시각이 아니라 그 시점의
    혼잡도를 반영해요.
    데이터가 없는 구간은 결과 리스트에서 빠집니다(=혼잡도 모름)."""
    data = _load_bus_ridership()
    if not data:
        return []

    base_total_min = hour * 60 + minute
    occupancy = []
    elapsed = 0
    for seg in sub_paths:
        if seg.get("traffic_type") == 2:  # 버스 구간만
            leg_total_min = (base_total_min + elapsed) % (24 * 60)
            leg_hour = leg_total_min // 60
            col = _boarding_column(leg_hour)

            route_no = (seg.get("lane_name") or "").strip()
            station = (seg.get("start_name") or "").strip()
            rows = data.get((route_no, station))
            if rows:
                values = []
                for row in rows:
                    raw = row.get(col)
                    if raw not in (None, ""):
                        try:
                            values.append(int(raw))
                        except ValueError:
                            pass
                if values:
                    avg_boarding = sum(values) / len(values)
                    if avg_boarding > 0:  # 0명이면 그 시간대엔 사실상 데이터가 없는 것 — 안내 안 함
                        occupancy.append({
                            "lane_name": seg.get("lane_name", ""),
                            "start_name": seg.get("start_name", ""),
                            "end_name": seg.get("end_name", ""),
                            "congestion": _congestion_label(avg_boarding),
                            "avg_boarding_count": round(avg_boarding),
                        })

        elapsed += seg.get("section_time_min", 0)

    return occupancy


def get_bus_congestion_trend_for_route(sub_paths, hour, minute):
    """경로의 버스 구간들에 대해, "지금 시간대"와 "다음 정시"의 평균 탑승 인원을
    비교해서 오르는지/내리는지 알려줍니다. (이 데이터는 1시간 단위라서,
    지하철 30분 단위 트렌드와 달리 "다음 슬롯"은 항상 다음 정시입니다.)

    검색 시각을 모든 구간에 그대로 쓰지 않고, 그 구간 앞에 있는 도보·지하철·
    버스 구간들의 소요시간을 누적해서 "실제로 그 구간에 도달하는 시각"을 구해
    사용합니다 — 환승 후 한참 뒤에 타는 버스 구간이라면, 그 시점 기준으로
    "다음 정시"까지 남은 시간과 그때의 혼잡도를 비교해요.

    각 항목은 {lane_name, start_name, end_name, current_congestion,
    next_congestion, diff_pct, direction, minutes_until_next} 형태입니다.
    (diff_pct는 지금 대비 다음 시간대의 상대적 증감률(%), direction은 "up"/"down")
    비교할 데이터가 없거나 변화가 없는 구간은 결과에서 빠집니다."""
    data = _load_bus_ridership()
    if not data:
        return []

    base_total_min = hour * 60 + minute
    trends = []
    elapsed = 0

    for seg in sub_paths:
        if seg.get("traffic_type") == 2:
            leg_total_min = base_total_min + elapsed
            leg_hour = (leg_total_min // 60) % 24
            leg_minute = leg_total_min % 60

            cur_col = _boarding_column(leg_hour)
            next_hour = (leg_hour + 1) % 24
            next_col = _boarding_column(next_hour)
            minutes_until_next = 60 - leg_minute if leg_minute != 0 else 60

            route_no = (seg.get("lane_name") or "").strip()
            station = (seg.get("start_name") or "").strip()
            rows = data.get((route_no, station))
            if rows:
                cur_values, next_values = [], []
                for row in rows:
                    raw_cur = row.get(cur_col)
                    raw_next = row.get(next_col)
                    if raw_cur not in (None, ""):
                        try:
                            cur_values.append(int(raw_cur))
                        except ValueError:
                            pass
                    if raw_next not in (None, ""):
                        try:
                            next_values.append(int(raw_next))
                        except ValueError:
                            pass

                if cur_values and next_values:
                    cur_avg = sum(cur_values) / len(cur_values)
                    next_avg = sum(next_values) / len(next_values)
                    if cur_avg != 0:  # 0명이면 "몇 % 변화"가 의미가 없어서 건너뜁니다.
                        diff_pct = round((next_avg - cur_avg) / cur_avg * 100)
                        # 다음 버스가 15분 이내에 올 때만 "오른다/낮아진다" 안내 자체를
                        # 보여줍니다 (버스 데이터는 1시간 단위라, 그보다 멀면 지금 알려줘도
                        # 실용성이 떨어져서요). 그 안에서, 낮아지는 경우에만 "N분 후
                        # 이동"을 추천하고, 오르는 경우엔 안내만 하고 추천 문구는 안 씁니다.
                        if diff_pct != 0 and minutes_until_next <= 15:
                            trends.append({
                                "lane_name": seg.get("lane_name", ""),
                                "start_name": seg.get("start_name", ""),
                                "end_name": seg.get("end_name", ""),
                                "current_congestion": _congestion_label(cur_avg),
                                "next_congestion": _congestion_label(next_avg),
                                "diff_pct": diff_pct,
                                "direction": "up" if diff_pct > 0 else "down",
                                "minutes_until_next": minutes_until_next,
                                "recommendation": (
                                    f"{minutes_until_next}분 후에 이동하는 것을 추천합니다"
                                    if diff_pct < 0 else None
                                ),
                            })

        elapsed += seg.get("section_time_min", 0)

    return trends


def get_gemini_general_recommendation(routes, occupancy_data, start, end, hour, minute, weekday):
    """일반 모드용 추천 결과를 반환합니다.
    ⚠️ 실제 Gemini 연동 전이라, 추천 순위는 항상 0번(첫 번째 경로)으로 고정하고,
    혼잡도가 있으면 그중 가장 붐비는 구간을 팁으로 짧게 안내합니다."""
    if not routes:
        return {
            "recommended_index": 0,
            "rush_hour_tip": "경로 정보가 없습니다.",
            "alternative": "",
        }

    if occupancy_data:
        order = {"혼잡": 2, "보통": 1, "여유": 0}
        worst = max(occupancy_data, key=lambda o: order.get(o["congestion"], 0))
        tip = (
            f"{worst['lane_name']}번 버스({worst['start_name']})가 이 시간대 평균 "
            f"{worst['avg_boarding_count']}명 탑승 — 혼잡도 '{worst['congestion']}'으로 보여요."
        )
    else:
        tip = "이 경로의 버스 구간에 대한 혼잡도 데이터가 없습니다."

    return {
        "recommended_index": 0,
        "rush_hour_tip": tip,
        "alternative": "",
    }
