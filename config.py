SERIAL_NUMBER = "GROWLAB-G111"

PC_IP      = "192.168.0.202"   # ← ipconfig로 확인 후 수정
SERVER_URL = f"http://{PC_IP}:8080"

MQTT_BROKER = PC_IP
MQTT_PORT   = 1883
MQTT_TOPIC_COMMAND        = f"growlab/{SERIAL_NUMBER}/command"
MQTT_TOPIC_PHOTO_INTERVAL = f"growlab/{SERIAL_NUMBER}/photo_interval"

SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 9600

LED_ON_HOUR  = 6
LED_OFF_HOUR = 22

CAMERA_INTERVAL_HOUR = 12

# ESP32-CAM mDNS 주소 (같은 WiFi 내에서 자동 해석)
ESP32CAM_URL = "http://growlab-cam.local"

# ── YOLO 모델 경로 (2개) ────────────────────────────────────────
# 생육 단계 탐지 모델 (sprout / growth / level 1~6 등)
YOLO_GROWTH_MODEL_PATH  = "/home/test123/Desktop/weights/best_ncnn_model"

# 질병 탐지 모델 (healthy / disease 등) — GERMINATION/MATURE 단계에서만 사용
YOLO_DISEASE_MODEL_PATH = "/home/test123/Desktop/dweights/best_ncnn_model"