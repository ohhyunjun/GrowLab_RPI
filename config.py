import os

SERIAL_NUMBER = "GROWLAB-G111"

SERVER_URL = "http://growlab-backend.ap-northeast-2.elasticbeanstalk.com"

# ── MQTT: EC2 Mosquitto TLS 브로커 ──────────────────────────────
MQTT_BROKER = "15.165.171.57"
MQTT_PORT   = 8883
MQTT_USER   = SERIAL_NUMBER
# 라즈베리파이에서만 설정한다. 이 파일을 공유하거나 저장소에 올리지 않는다.
MQTT_PASS   = "SET_DEVICE_MQTT_PASSWORD_ON_RASPBERRY_PI"
MQTT_CA_CERT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "certs",
    "growlab-mqtt-ca.crt",
)

MQTT_TOPIC_COMMAND        = f"growlab/{SERIAL_NUMBER}/command"
MQTT_TOPIC_PHOTO_INTERVAL = f"growlab/{SERIAL_NUMBER}/photo_interval"
MQTT_TOPIC_CULTIVATION_CONFIG = f"growlab/{SERIAL_NUMBER}/cultivation-config"

SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 9600

LED_ON_HOUR  = 6
LED_OFF_HOUR = 22

CAMERA_INTERVAL_HOUR = 12

# 생육 모델이 이 값 이상일 때만 "변화 후보"로 취급한다.
YOLO_CONFIRMATION_MIN_CONFIDENCE = 0.80
YOLO_CONFIRMATION_ATTEMPTS = 3
YOLO_CONFIRMATION_INTERVAL_SECONDS = 5

# RPi가 포트별 마지막 확정 생육 결과를 보관하는 로컬 파일이다.
# 서버 PlantStage가 아니라, 재촬영 필요 여부를 판단하는 YOLO 결과 캐시다.
VISION_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vision_state.json",
)

# ESP32-CAM mDNS 주소 (같은 WiFi 내에서 자동 해석)
ESP32CAM_URL = "http://growlab-cam.local"

# ── YOLO 모델 경로 (2개) ────────────────────────────────────────
# 생육 단계 탐지 모델 (sprout / growth / level 1~6 등)
YOLO_GROWTH_MODEL_PATH  = "/home/test123/Desktop/weights/best_ncnn_model"
# 질병 탐지 모델 (healthy / disease 등) — GERMINATION/MATURE 단계에서만 사용
YOLO_DISEASE_MODEL_PATH = "/home/test123/Desktop/dweights/best_ncnn_model"
