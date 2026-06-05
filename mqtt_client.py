import threading
import logging
import paho.mqtt.client as mqtt
from config import MQTT_BROKER, MQTT_PORT, MQTT_TOPIC_COMMAND, MQTT_TOPIC_PHOTO_INTERVAL

logger = logging.getLogger(__name__)

class MqttClient:
    def __init__(self, on_command_cb, on_photo_interval_cb):
        """
        서버 MqttPublisher 토픽 구조:
          growlab/{serial}/command        → LED 명령
            "O"                           → LED ON  (수동)
            "o"                           → LED OFF (수동)
            "SCHED:06:00-22:00"           → LED 자동 스케줄
          growlab/{serial}/photo_interval → 촬영 주기 (시간 단위 숫자 문자열)

        on_command_cb(cmd: str)           → LedScheduler 연결
        on_photo_interval_cb(hours: int)  → MotorScheduler 연결
        """
        self.on_command_cb        = on_command_cb
        self.on_photo_interval_cb = on_photo_interval_cb

        self._client = mqtt.Client()
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

    def start(self):
        try:
            self._client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            t = threading.Thread(target=self._client.loop_forever, daemon=True)
            t.start()
            logger.info(f"[MqttClient] 브로커 연결 시도: {MQTT_BROKER}:{MQTT_PORT}")
        except Exception as e:
            logger.error(f"[MqttClient] 연결 실패: {e}")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(MQTT_TOPIC_COMMAND)
            client.subscribe(MQTT_TOPIC_PHOTO_INTERVAL)
            logger.info("[MqttClient] 연결 성공")
            logger.info(f"[MqttClient] 구독: {MQTT_TOPIC_COMMAND}")
            logger.info(f"[MqttClient] 구독: {MQTT_TOPIC_PHOTO_INTERVAL}")
        else:
            logger.error(f"[MqttClient] 연결 실패 rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        logger.warning(f"[MqttClient] 연결 끊김 rc={rc}")

    def _on_message(self, client, userdata, msg):
        topic   = msg.topic
        payload = msg.payload.decode().strip()
        logger.info(f"[MqttClient] 수신 topic={topic} payload={payload}")

        if topic == MQTT_TOPIC_COMMAND:
            self.on_command_cb(payload)

        elif topic == MQTT_TOPIC_PHOTO_INTERVAL:
            try:
                hours = int(payload)
                self.on_photo_interval_cb(hours)
            except ValueError:
                logger.error(f"[MqttClient] photo_interval 파싱 오류: {payload}")
