import logging
import ssl
import paho.mqtt.client as mqtt
from config import (
    MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS,
    MQTT_CA_CERT_PATH, MQTT_TOPIC_COMMAND, MQTT_TOPIC_PHOTO_INTERVAL,
)

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

        EC2 Mosquitto 기준: TLS + 계정(ID/PW) 인증 + ACL(RPi는 구독 전용).
        """
        self.on_command_cb        = on_command_cb
        self.on_photo_interval_cb = on_photo_interval_cb

        self._client = mqtt.Client()
        self._client.username_pw_set(MQTT_USER, MQTT_PASS)
        self._client.tls_set(
            ca_certs=MQTT_CA_CERT_PATH,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self._client.tls_insecure_set(False)

        # 재연결 백오프: 1초 → 최대 30초로 점진 증가 (와이파이 끊김 대응)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

    def start(self):
        try:
            # Wi-Fi가 초기 연결 시점에 끊겨 있어도 loop_start가 재연결을 계속 시도한다.
            self._client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
            self._client.loop_start()
            logger.info(f"[MqttClient] 브로커 연결 시도: {MQTT_BROKER}:{MQTT_PORT}")
        except Exception as e:
            logger.error(f"[MqttClient] 연결 실패: {e}")

    def stop(self):
        self._client.disconnect()
        self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(MQTT_TOPIC_COMMAND, qos=1)
            client.subscribe(MQTT_TOPIC_PHOTO_INTERVAL, qos=1)
            logger.info("[MqttClient] 연결 성공, 구독 완료")
            logger.info(f"[MqttClient] 구독: {MQTT_TOPIC_COMMAND}")
            logger.info(f"[MqttClient] 구독: {MQTT_TOPIC_PHOTO_INTERVAL}")
        elif rc == 5:
            logger.error("[MqttClient] 인증 실패(rc=5) — MQTT_USER/MQTT_PASS 또는 브로커 ACL 확인 필요")
        else:
            logger.error(f"[MqttClient] 연결 실패 rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        logger.warning(f"[MqttClient] 연결 끊김 rc={rc}, 재연결 대기 중")

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
