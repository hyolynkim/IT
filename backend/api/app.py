from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sys
import requests
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from models.route_finder import find_cat_optimal_route

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def is_rush_hour(hour, minute, weekday):
    return True  # 테스트용

def get_weekday_korean(weekday):
    days = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    return days[weekday] if 0 <= weekday <= 6 else "평일"

def get_rush_hour_type(hour, minute, weekday):
    total_min = hour * 60 + minute
    if 5 * 60 + 30 <= total_min <= 7 * 60 + 30:
        return "출근 러시아워"
    elif 16 * 60 + 30 <= total_min <= 19 * 60 + 30:
        return "퇴근 러시아워"
    elif 21 * 60 <= total_min <= 23 * 60:
        return "심야 러시아워"
    return "러시아워"

def get_gemini_rush_hour_recommendation(routes, start, end, hour, minute, weekday):
    if not GEMINI_API_KEY:
        return {
            "recommended_index": 0,
            "rush_hour_tip": "API 키 설정 후 러시아워 분석이 제공됩니다.",
            "alternative": ""
        }

    try:
        weekday_str = get_weekday_korean(weekday)
        rush_type = get_rush_hour_type(hour, minute, weekday)

        routes_summary = []
        for i, r in enumerate(routes[:3]):
            sub_paths = r.get("sub_paths", [])
            path_desc = " → ".join([
                f"{s.get('start_name', '')}({s.get('lane_name', '')})"
                for s in sub_paths if s.get('traffic_type') != 3
            ]) or f"{r.get('first_start_station', '')} → {r.get('last_end_station', '')}"

            routes_summary.append({
                "index": i,
                "description": path_desc,
                "time_min": r.get("estimated_comfort_time_min"),
                "original_time_min": r.get("original_time_min"),
                "transfer_count": r.get("transfer_count", 0),
                "has_express_bus": r.get("has_express_bus", False),
                "payment_krw": r.get("payment_krw", 0)
            })

        prompt = f"""
당신은 한국 수도권 대중교통 러시아워 전문가입니다.

현재 상황:
- 현재 시각: {hour}시 {minute}분
- 요일: {weekday_str}
- 시간대: {rush_type}
- 출발지: {start}
- 도착지: {end}

분석할 경로 목록:
{json.dumps(routes_summary, ensure_ascii=False, indent=2)}

위 정보를 바탕으로 다음을 분석해주세요:
1. {rush_type} 시간대의 일반적인 광역버스 혼잡 패턴 고려
2. 혼잡할 경우 1~2개 전 정거장에서 탑승하는 것이 유리한지 판단
3. 버스보다 지하철이 더 나은 대안인지 판단
4. {weekday_str} {hour}시의 실제 교통 패턴 반영

반드시 아래 JSON 형식으로만 응답 (다른 텍스트 없이):
{{
  "recommended_index": 0,
  "rush_hour_tip": "구체적인 러시아워 팁 (한국어, 2문장, 정거장명 포함)",
  "alternative": "대안 제안 (한국어, 없으면 빈 문자열)"
}}
"""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 500
            }
        }
        response = requests.post(url, json=payload, timeout=10)

        if response.status_code == 200:
            result = response.json()
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        else:
            print(f"Gemini API 상태코드: {response.status_code}, 응답: {response.text}")
            return {
                "recommended_index": 0,
                "rush_hour_tip": f"Gemini API 오류 ({response.status_code})",
                "alternative": ""
            }

    except Exception as e:
        print(f"Gemini 에러: {str(e)}")
        return {
            "recommended_index": 0,
            "rush_hour_tip": f"분석 중 오류: {str(e)}",
            "alternative": ""
        }

@app.route('/predict/congestion', methods=['POST'])
def predict_congestion():
    data = request.get_json()
    if not data or 'passenger_count' not in data:
        return jsonify({"error": "passenger_count 파라미터가 필요합니다."}), 400
    count = data['passenger_count']
    if count > 800:
        result = {"status": "혼잡", "code": 2}
    elif count > 300:
        result = {"status": "보통", "code": 1}
    else:
        result = {"status": "여유", "code": 0}
    return jsonify({"passenger_count": count, "prediction": result})

@app.route('/api/routes', methods=['GET'])
def get_optimal_route():
    start = request.args.get('start', '성신여대입구')
    end = request.args.get('end', '기흥역')
    hour = request.args.get('hour', default=9, type=int)
    minute = request.args.get('minute', default=0, type=int)
    weekday = request.args.get('weekday', default=0, type=int)

    final_result = find_cat_optimal_route(start, end, hour)

    if final_result.get("status") == "fail":
        return jsonify(final_result)

    routes = final_result.get("routes", [])

    rush_hour = is_rush_hour(hour, minute, weekday)
    rush_hour_result = None

    if rush_hour and routes:
        rush_hour_result = get_gemini_rush_hour_recommendation(
            routes, start, end, hour, minute, weekday
        )

    final_result["is_rush_hour"] = rush_hour
    final_result["rush_hour_result"] = rush_hour_result

    return jsonify(final_result)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)