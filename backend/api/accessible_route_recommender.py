"""
accessible_route_recommender.py
--------------------------------
임산부, 노약자 등 이동 약자를 배려한 경로를 추천하는 스크립트.

구조:
1) Google Maps Directions API로 출발지-목적지 간 '실제 경로 후보'(도보/대중교통 등)를 가져온다.
   - Gemini는 실시간 지도/교통 데이터를 갖고 있지 않으므로, 실제 경로 데이터를 먼저 확보해야 한다.
2) 각 경로의 거리, 소요시간, 도보 구간, 환승 횟수, 지하철 노선/하차역 등을 정리한다.
3) Gemini API에 "임산부/노약자 관점"에서 각 경로를 4개 카테고리(일반/최소도보/최소환승/계단회피)로
   나누어 추천하도록 요청한다.
4) 추천된 경로에 지하철 구간이 있으면, subway_elevator_guide.py의 서울교통공사
   빠른하차정보 API를 호출해서 엘리베이터(또는 에스컬레이터) 인접 하차칸을 함께 안내한다.

"""

import os
import sys
import json
import requests

# ─────────────────────────────────────────────────────────
GEMINI_API_KEY = ""       # 예: "AIzaSy...."
GOOGLE_MAPS_API_KEY = ""  # 예: "AIzaSy...." (없으면 빈 문자열로 두면 예시 데이터로 동작)
# ─────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)
MAPS_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


def get_routes_from_google_maps(origin: str, destination: str, api_key: str) -> list:
    """
    Google Maps Directions API를 이용해 대중교통 기준 경로 후보(alternatives)를 가져온다.
    각 경로에서 이동약자와 관련된 핵심 정보(총 거리, 총 시간, 도보 구간, 환승 수)를 추출한다.
    """
    params = {
        "origin": origin,
        "destination": destination,
        "mode": "transit",
        "alternatives": "true",
        "language": "ko",
        "key": api_key,
    }
    resp = requests.get(MAPS_DIRECTIONS_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "OK":
        raise RuntimeError(f"Google Maps API 오류: {data.get('status')} - {data.get('error_message', '')}")

    routes = []
    for route in data.get("routes", []):
        leg = route["legs"][0]
        total_distance_m = leg["distance"]["value"]
        total_duration_s = leg["duration"]["value"]

        walking_distance_m = 0
        transfer_count = 0
        steps_summary = []

        for step in leg["steps"]:
            if step["travel_mode"] == "WALKING":
                walking_distance_m += step["distance"]["value"]
            if step["travel_mode"] == "TRANSIT":
                transfer_count += 1

            step_info = {
                "mode": step["travel_mode"],
                "instruction": step.get("html_instructions", ""),
                "distance_m": step["distance"]["value"],
                "duration_s": step["duration"]["value"],
            }

            if step["travel_mode"] == "TRANSIT":
                # 지하철 하차칸 안내 API 연동을 위해 노선명/하차역/차량 종류를 함께 저장
                transit_details = step.get("transit_details", {})
                line_info = transit_details.get("line", {})
                vehicle_info = line_info.get("vehicle", {})
                step_info["transit_line"] = line_info.get("short_name") or line_info.get("name", "")
                step_info["vehicle_type"] = vehicle_info.get("type", "")
                step_info["arrival_stop"] = transit_details.get("arrival_stop", {}).get("name", "")
                step_info["departure_stop"] = transit_details.get("departure_stop", {}).get("name", "")

            steps_summary.append(step_info)

        routes.append(
            {
                "summary": route.get("summary", ""),
                "total_distance_m": total_distance_m,
                "total_duration_min": round(total_duration_s / 60, 1),
                "walking_distance_m": walking_distance_m,
                "transfer_count": max(transfer_count - 1, 0),
                "steps": steps_summary,
            }
        )
    return routes


def get_sample_routes() -> list:
    """
    GOOGLE_MAPS_API_KEY가 없을 때 동작 확인용으로 사용하는 예시 경로 데이터.
    (하차칸 API 테스트를 위해 transit_line / arrival_stop / vehicle_type 필드 포함)
    """
    return [
        {
            "summary": "지하철 1호선 경유",
            "total_distance_m": 9800,
            "total_duration_min": 32,
            "walking_distance_m": 850,
            "transfer_count": 1,
            "steps": [
                {"mode": "WALKING", "instruction": "역까지 도보 이동 (계단 있음)", "distance_m": 400, "duration_s": 360},
                {
                    "mode": "TRANSIT",
                    "instruction": "1호선 탑승",
                    "distance_m": 8600,
                    "duration_s": 1500,
                    "transit_line": "1호선",
                    "vehicle_type": "SUBWAY",
                    "departure_stop": "서울역",
                    "arrival_stop": "시청역",
                },
                {"mode": "WALKING", "instruction": "환승 후 목적지까지 도보 (엘리베이터 있음)", "distance_m": 450, "duration_s": 420},
            ],
        },
        {
            "summary": "버스 환승 경유",
            "total_distance_m": 10500,
            "total_duration_min": 40,
            "walking_distance_m": 300,
            "transfer_count": 0,
            "steps": [
                {"mode": "WALKING", "instruction": "정류장까지 도보 이동 (평지)", "distance_m": 150, "duration_s": 120},
                {
                    "mode": "TRANSIT",
                    "instruction": "버스 탑승",
                    "distance_m": 10200,
                    "duration_s": 2160,
                    "transit_line": "402번",
                    "vehicle_type": "BUS",
                    "departure_stop": "서울역버스환승센터",
                    "arrival_stop": "강남역",
                },
                {"mode": "WALKING", "instruction": "목적지까지 도보 (평지)", "distance_m": 150, "duration_s": 120},
            ],
        },
    ]


def build_prompt(origin: str, destination: str, routes: list) -> str:
    """
    Gemini에게 전달할 프롬프트를 구성한다.
    단일 추천이 아니라, 아래 4개 카테고리별로 가장 적합한 경로를 각각 골라달라고 요청한다.
    - general      : 종합적으로 무난한 일반 추천 경로
    - min_walking  : 도보 거리가 가장 짧은 경로
    - min_transfer : 환승 횟수가 가장 적은 경로
    - avoid_stairs : 계단이 가장 적고 엘리베이터/에스컬레이터 이용이 쉬운 경로
    """
    routes_json = json.dumps(routes, ensure_ascii=False, indent=2)

    prompt = f"""당신은 교통약자(임산부, 노약자, 거동이 불편한 사람)를 위한 경로 추천 전문가입니다.

출발지: {origin}
목적지: {destination}

아래는 실제 대중교통 경로 후보 데이터입니다:
{routes_json}

각 경로 데이터의 "summary" 값을 식별자로 사용해서, 아래 4가지 관점별로
가장 적합한 경로를 "각각" 하나씩 골라주세요. (같은 경로가 여러 카테고리에 중복 선택되어도 괜찮습니다)

1. general      : 도보 거리, 환승, 소요시간, 계단 여부를 종합적으로 고려했을 때 가장 무난하고 균형 잡힌 경로
2. min_walking  : walking_distance_m 이 가장 작은 경로 (도보 부담 최소화)
3. min_transfer : transfer_count 가 가장 작은 경로 (환승 부담 최소화)
4. avoid_stairs : steps의 instruction 텍스트에서 "계단"이 적고 "엘리베이터"/"에스컬레이터" 관련 언급이 많은 경로

반드시 아래 JSON 형식으로만 응답하고, 그 외 텍스트는 절대 포함하지 마세요:
{{
  "categories": [
    {{
      "category_key": "general",
      "category_label": "일반 추천 경로",
      "route_summary": "해당 경로의 summary 값",
      "reason": "이 경로를 이 카테고리로 고른 이유 (한국어, 2~3문장, 임산부/노약자 관점)"
    }},
    {{
      "category_key": "min_walking",
      "category_label": "최소 도보 경로",
      "route_summary": "해당 경로의 summary 값",
      "reason": "이유 (한국어, 2~3문장)"
    }},
    {{
      "category_key": "min_transfer",
      "category_label": "최소 환승 경로",
      "route_summary": "해당 경로의 summary 값",
      "reason": "이유 (한국어, 2~3문장)"
    }},
    {{
      "category_key": "avoid_stairs",
      "category_label": "계단 회피 경로",
      "route_summary": "해당 경로의 summary 값",
      "reason": "이유 (한국어, 2~3문장)"
    }}
  ],
  "overall_caution": "전체적으로 임산부/노약자가 주의하거나 미리 준비하면 좋은 점 (한국어, 1~2문장)"
}}
"""
    return prompt


def call_gemini(prompt: str, api_key: str) -> dict:
    """Gemini API를 호출하고 JSON 응답을 파싱해서 반환한다."""
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(
        f"{GEMINI_API_URL}?key={api_key}",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini 응답 파싱 실패: {data}") from e

    text = text.strip().strip("```json").strip("```").strip()
    return json.loads(text)


def find_route_by_summary(routes: list, summary: str) -> dict:
    """summary 값으로 원본 경로 데이터를 찾는다. 못 찾으면 None."""
    for r in routes:
        if r.get("summary") == summary:
            return r
    return None


def get_subway_legs(route: dict) -> list:
    """
    경로 안에서 '지하철'로 판단되는 TRANSIT 구간만 골라
    (노선명, 하차역) 쌍의 리스트로 반환한다.
    버스 등 지하철이 아닌 TRANSIT 구간은 제외한다.
    """
    if not route:
        return []

    subway_vehicle_types = {"SUBWAY", "HEAVY_RAIL", "METRO_RAIL", "COMMUTER_TRAIN"}
    legs = []
    for step in route.get("steps", []):
        if step.get("mode") != "TRANSIT":
            continue
        vehicle_type = step.get("vehicle_type", "")
        line_name = step.get("transit_line", "")
        arrival_stop = step.get("arrival_stop", "")

        is_subway = vehicle_type in subway_vehicle_types or "지하철" in step.get("instruction", "")
        if is_subway and line_name and arrival_stop:
            legs.append((line_name, arrival_stop))
    return legs


def get_elevator_friendly_car(line_name: str, station_name: str) -> dict:
    """
    subway_elevator_guide.py 의 fetch_quick_get_off_info()를 호출해서
    엘리베이터(또는 대체 설비) 인접 하차칸 정보를 가져온다.

    반환 형식:
    {"status": "ok" | "not_found" | "no_facility" | "error" | "unavailable", "message": "..."}
    """
    try:
        from subway_elevator_guide import fetch_quick_get_off_info
    except ImportError:
        return {
            "status": "unavailable",
            "message": "subway_elevator_guide.py 파일을 찾을 수 없습니다. "
                       "이 스크립트와 같은 폴더에 있는지 확인해주세요.",
        }

    if not station_name.endswith("역"):
        station_name += "역"

    info = fetch_quick_get_off_info(line_name, station_name)

    if info is None:
        return {
            "status": "error",
            "message": "하차칸 API 호출에 실패했습니다. 서비스키(SUBWAY_API_KEY)와 "
                       "인터넷 연결을 확인해주세요.",
        }
    if not info.station_found:
        return {
            "status": "not_found",
            "message": f"{line_name} {station_name} 정보를 찾을 수 없습니다.",
        }
    if not info.directions:
        return {
            "status": "no_facility",
            "message": f"{line_name} {station_name}에는 등록된 엘리베이터/에스컬레이터 정보가 없습니다.",
        }
    return {"status": "ok", "message": info.guide_message()}


def print_categorized_result(routes: list, result: dict) -> None:
    print("\n=== 경로 후보 요약 ===")
    for r in routes:
        print(
            f"- {r['summary']}: 총 {r['total_duration_min']}분, "
            f"도보 {r['walking_distance_m']}m, 환승 {r['transfer_count']}회"
        )

    print("\n=== 임산부/노약자 맞춤 카테고리별 추천 ===")
    for cat in result.get("categories", []):
        label = cat.get("category_label", cat.get("category_key", ""))
        summary = cat.get("route_summary", "")
        reason = cat.get("reason", "")

        print(f"\n[{label}] → {summary}")
        print(f"  이유: {reason}")

        matched_route = find_route_by_summary(routes, summary)
        subway_legs = get_subway_legs(matched_route)
        for line_name, station_name in subway_legs:
            car_info = get_elevator_friendly_car(line_name, station_name)
            print(f"\n  🚇 {line_name} {station_name} 하차 안내:")
            for text_line in car_info["message"].split("\n"):
                print(f"    {text_line}")

    print(f"\n전체 주의 사항: {result.get('overall_caution')}")


def get_stations_from_args_or_input() -> tuple:
    """
    커맨드라인 인자로 출발지/목적지가 주어지면 그것을 쓰고,
    없으면 실행 중에 직접 입력받는다. (역 이름만 쳐도 바로 동작하게 하기 위함)
    """
    if len(sys.argv) >= 3:
        return sys.argv[1], sys.argv[2]

    print("출발지/목적지를 입력받아 진행합니다.")
    origin = input("출발지를 입력하세요 (예: 서울역): ").strip()
    destination = input("목적지를 입력하세요 (예: 강남역): ").strip()
    return origin, destination


def main():
    print("=" * 50, flush=True)
    print("임산부/노약자 맞춤 경로 추천 프로그램", flush=True)
    print("=" * 50, flush=True)

    origin, destination = get_stations_from_args_or_input()
    if not origin or not destination:
        print("출발지와 목적지를 모두 입력해야 합니다. 프로그램을 종료합니다.", flush=True)
        return

    print(f"\n[출발지: {origin} → 목적지: {destination}] 경로를 찾는 중입니다...", flush=True)

    # 키를 찾는 우선순위: 1) 코드 상단에 직접 적어둔 값  2) 환경 변수
    # (더 이상 실행 중에 키를 입력받지 않습니다. 파일 상단 GEMINI_API_KEY / GOOGLE_MAPS_API_KEY에 미리 채워두세요.)
    gemini_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print(
            "Gemini API 키가 설정되어 있지 않습니다. "
            "파일 상단의 GEMINI_API_KEY 변수에 키를 채워 넣거나 "
            "환경변수 GEMINI_API_KEY를 설정한 뒤 다시 실행해주세요.",
            flush=True,
        )
        return

    maps_key = GOOGLE_MAPS_API_KEY or os.environ.get("GOOGLE_MAPS_API_KEY")

    try:
        if maps_key:
            print("Google Maps API로 실제 경로를 조회합니다...", flush=True)
            routes = get_routes_from_google_maps(origin, destination, maps_key)
        else:
            print("Google Maps API 키가 없어 예시 데이터로 동작합니다. (실제 서비스에는 지도 API 키를 설정하세요)", flush=True)
            routes = get_sample_routes()

        if not routes:
            print("경로를 찾지 못했습니다. 출발지/목적지 이름을 확인해주세요.", flush=True)
            return

        print("Gemini에게 경로 분석을 요청합니다... (몇 초 정도 걸릴 수 있습니다)", flush=True)
        prompt = build_prompt(origin, destination, routes)
        result = call_gemini(prompt, gemini_key)

        print_categorized_result(routes, result)

    except Exception as e:
        # 에러가 나도 창이 바로 닫히지 않도록 여기서 잡아서 보여준다.
        print(f"\n[오류 발생] {type(e).__name__}: {e}", flush=True)
        print("API 키가 올바른지, 인터넷 연결이 되어 있는지 확인해주세요.", flush=True)

    finally:
        input("\n엔터를 누르면 종료합니다...")


if __name__ == "__main__":
    main()