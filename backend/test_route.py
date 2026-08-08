# backend/test_route.py
print("=== 스크립트 시작 ===", flush=True)
import json
from models.route_finder import find_cat_optimal_route
from services.general_route import get_bus_occupancy_for_route
print("=== import 완료 ===", flush=True)

result = find_cat_optimal_route("성신여대입구", "기흥역", 9, mode="general")

routes = result.get("routes", [])
if routes:
    sub_paths = routes[0].get("sub_paths", [])
    print("=== sub_paths ===", flush=True)
    print(json.dumps(sub_paths, ensure_ascii=False, indent=2))

    print("=== 버스 여석 조회 시작 ===", flush=True)
    occupancy = get_bus_occupancy_for_route(sub_paths)
    print("=== 버스 여석 결과 ===", flush=True)
    print(json.dumps(occupancy, ensure_ascii=False, indent=2))
else:
    print("경로 결과 없음:", result)