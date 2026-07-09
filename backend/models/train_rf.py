# backend/models/train_rf.py
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
import joblib
import os

def train_congestion_model():
    # 1. 데이터 불러오기
    data_path = 'backend/data/subway_passenger.csv'
    if not os.path.exists(data_path):
        print("데이터 파일이 없습니다. 먼저 데이터를 생성하거나 수집해주세요.")
        return
    
    df = pd.read_csv(data_path)
    
    # 2. 혼잡도 라벨링 (수요 예측 및 라벨링 기준 정하기)
    # 승객 수에 따라 여유(0), 보통(1), 혼잡(2)으로 분류 기준 설정
    def label_congestion(count):
        if count > 800:
            return 2  # 혼잡
        elif count > 300:
            return 1  # 보통
        else:
            return 0  # 여유
            
    df['congestion_level'] = df['passenger_count'].apply(label_congestion)
    
    # 3. 학습 피처 및 타겟 설정
    # 여기서는 단순하게 '시간(hour)'을 기반으로 혼잡도를 분류하는 모델을 만듭니다.
    X = df[['hour']] 
    y = df['congestion_level']
    
    # 데이터 쪼개기
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_split=0.2, random_state=42)
    
    # 4. Random Forest 모델 생성 및 학습
    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)
    
    # 모델 저장할 폴더 생성 및 저장
    os.makedirs('backend/models/saved_models', exist_ok=True)
    joblib.dump(model, 'backend/models/saved_models/rf_congestion.pkl')
    print("Random Forest 혼잡도 분류 모델 학습 및 저장 완료!")

def predict_congestion(hour):
    # 저장된 모델을 불러와 실시간/예상 혼잡도를 예측하는 함수
    model_path = 'backend/models/saved_models/rf_congestion.pkl'
    if os.path.exists(model_path):
        model = joblib.load(model_path)
        pred = model.predict([[hour]])[0]
        status_map = {0: '여유', 1: '보통', 2: '혼잡'}
        return status_map[pred]
    else:
        return '보통' # 모델이 없을 때의 기본 디폴트값

if __name__ == '__main__':
    train_congestion_model()