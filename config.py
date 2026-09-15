import os

SERIAL_NUMBER = "GROWLAB-G111"

SERVER_URL = "http://growlab-backend.ap-northeast-2.elasticbeanstalk.com"

# ── MQTT: EC2 Mosquitto TLS 브로커 ──────────────────────────────
MQTT_BROKER = "15.165.171.57"
MQTT_PORT   = 8883
MQTT_USER   = SERIAL_NUMBER

# 비밀번호는 환경변수에서 읽는다. 저장소에 실값을 넣지 않기 위함이며,
# 라즈베리파이의 systemd 유닛이나 ~/.bashrc에 GROWLAB_MQTT_PASS를 설정한다.
MQTT_PASS = os.environ.get("GROWLAB_MQTT_PASS", "")

MQTT_CA_CERT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "certs",
    "growlab-mqtt-ca.crt",
)

MQTT_TOPIC_COMMAND        = f"growlab/{SERIAL_NUMBER}/command"
MQTT_TOPIC_PHOTO_INTERVAL = f"growlab/{SERIAL_NUMBER}/photo_interval"

# ── 아두이노 (UNO R4) ───────────────────────────────────────────
# udev 규칙(99-growlab.rules)으로 심볼릭 링크를 고정한다.
SERIAL_PORT = "/dev/growlab-arduino"
SERIAL_BAUD = 9600

# ── ESP32-CAM (USB UART) ────────────────────────────────────────
# [WiFi 모드 흔적] ESP32-CAM은 2.4GHz 전용이라 5GHz AP 환경에서 연결 불가.
#                 USB-TTL(UART) 직결 방식으로 전환했다.
# ESP32CAM_URL = "http://growlab-cam.local"
ESP32CAM_PORT = "/dev/growlab-cam"
ESP32CAM_BAUD = 460800

LED_ON_HOUR  = 6
LED_OFF_HOUR = 22

CAMERA_INTERVAL_HOUR = 12

# ── YOLO 생육 단계 변화 검증 ───────────────────────────────────
# 이전 확정 단계보다 앞선 단계가 처음 보이면 같은 포트에서 총 3회 촬영하고,
# 이 신뢰도 이상인 동일 결과가 2회 이상일 때만 서버에 확정 결과를 전송한다.
YOLO_CONFIRMATION_MIN_CONFIDENCE = 0.70
YOLO_CONFIRMATION_ATTEMPTS = 3
YOLO_CONFIRMATION_INTERVAL_SECONDS = 5

# ── 센서 이상 임계값 ────────────────────────────────────────────
# 아두이노는 부팅 직후 임계값이 전부 NAN이라 CFG:를 받기 전까지 [ALERT]를
# 내보내지 않는다. RPi가 연결 직후 이 값을 CFG로 전송해 활성화한다.
# 작물별 임계값이 도입되면 백엔드가 MQTT로 내려주는 값으로 대체한다.
# None은 "제한 없음"을 뜻하며 CFG에서 NA로 직렬화된다.
ALERT_THRESHOLDS = {
    "TEMP": (15.0,  30.0),
    "HUM":  (30.0,  90.0),
    "PH":   ( 5.0,   7.5),
    "TDS":  (200.0, 800.0),
}

# CFG 문자열의 값 순서 (아두이노 applyCropThresholdConfig와 일치해야 함)
ALERT_CFG_ORDER = ["TEMP", "HUM", "PH", "TDS"]

# ── YOLO 모델 경로 (2개) ────────────────────────────────────────
# 생육 단계 탐지 모델 (sprout / growth / level 1~6 등)
YOLO_GROWTH_MODEL_PATH  = "/home/test123/Desktop/weights/best_ncnn_model"
# 질병 탐지 모델 (healthy / disease 등) — GERMINATION/MATURE 단계에서만 사용
YOLO_DISEASE_MODEL_PATH = "/home/test123/Desktop/dweights/best_ncnn_model"


def build_cfg_command() -> str:
    """ALERT_THRESHOLDS를 아두이노 CFG: 명령 문자열로 직렬화한다."""
    parts = []
    for key in ALERT_CFG_ORDER:
        low, high = ALERT_THRESHOLDS[key]
        parts.append("NA" if low  is None else f"{low:.1f}")
        parts.append("NA" if high is None else f"{high:.1f}")
    return "CFG:" + ",".join(parts)

# ══════════════════════════════════════════════════════════════
# 기기 자가진단 (주 1회)
# 지정 요일/시각에 아두이노 SELFTEST + 카메라 PING으로 기기 상태 점검.
# weekday: 월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6
# 액추에이터(NEMA17/23)는 리미트 스위치가 없어 물리검증 불가 → 진단 제외.
# ══════════════════════════════════════════════════════════════
SELFTEST_WEEKDAY      = 6      # 일요일
SELFTEST_HOUR         = 3      # 새벽 3시 (촬영/활동 없는 시간)
SELFTEST_CAMERA_CHECK = True   # 카메라 PING 포함 여부

# ══════════════════════════════════════════════════════════════
# [테스트] pH 이상 시 아두이노 1회 리셋
# 아두이노가 보낸 [DATA] pH가 이 값을 넘으면(뻥튀기) 아두이노를 소프트 리셋('R').
# 이번엔 "리셋이 실제로 되는지" 확인하는 테스트 목적이라, 실행 중 딱 1회만 한다.
# ══════════════════════════════════════════════════════════════
PH_RESET_TRIGGER_ABOVE = 11.0
