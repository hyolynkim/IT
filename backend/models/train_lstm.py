import os
import pandas as pd
import numpy as np
import joblib
from sklearn.preprocessing import MinMaxScaler

def create_dataset(dataset, look_back=1):
    X, Y = [], []
    for i in range(len(dataset) - look_back):
        X.append(dataset[i:(i + look_back), 0])
        Y.append(dataset[i + look_back, 0])
    return np.array(X), np.array(Y)

def train_lstm_model():
    print("--- LSTM 시계열 데이터 불러오기 및 전처리 ---")
    data_path = 'backend/data/subway_passenger.csv'
    
    if not os.path.exists(data_path):
        print(f"데이터 파일이 없습니다: {data_path}")
        return

    df = pd.read_csv(data_path)
    passenger_data = df[['count']].values.astype('float32')
    
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled_data = scaler.fit_transform(passenger_data)
    
    look_back = 3
    X, Y = create_dataset(scaled_data, look_back)
    
    joblib.dump(scaler, 'backend/models/lstm_scaler.joblib')
    print("LSTM 모델 파이프라인 가공 완료! (models/lstm_scaler.joblib 저장됨)")

if __name__ == "__main__":
    train_lstm_model()