import requests
import logging
from config import ESP32CAM_URL

logger = logging.getLogger(__name__)

def capture() -> bytes:
    """ESP32-CAM에서 이미지 캡처 → JPEG bytes 반환. 실패 시 None 반환."""
    try:
        res = requests.get(f"{ESP32CAM_URL}/capture", timeout=10)
        res.raise_for_status()
        logger.info("[CameraClient] 캡처 성공")
        return res.content
    except Exception as e:
        logger.error(f"[CameraClient] 캡처 실패: {e}")
        return None
