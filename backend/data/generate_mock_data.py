# backend/data/generate_mock_data.py
import pandas as pd
import numpy as np
import os

# 데이터 폴더 생성
os.makedirs('backend/data', exist_ok=True)

# 시간대 배열 (0시 ~ 23시)
hours = list(range(24))
stations = ['성신여대입구', '수유', '서울역', '기흥']

# 1. 지하철 샘플 데이터 생성
subway_data = []
for station in stations:
    for hour in hours:
        # 출퇴근 시간(8시, 18시)에 승객 수가 급증하는 패턴 생성
        if hour in [8, 9, 18, 19]:
            base_passenger = np.random.randint(800, 1500)
        else:
            base_passenger = np.random.randint(50, 300)
            
        subway_data.append({
            'station_name': station,
            'hour': hour,
            'passenger_count': base_passenger,
            'is_weekend': 0
        })

df_subway = pd.DataFrame(subway_data)
df_subway.to_csv('backend/data/subway_passenger.csv', index=False, encoding='utf-8-sig')
print("지하철 샘플 데이터 생성 완료: backend/data/subway_passenger.csv")