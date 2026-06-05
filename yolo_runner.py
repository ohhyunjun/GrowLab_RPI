import logging
import numpy as np
import cv2
from config import YOLO_GROWTH_MODEL_PATH, YOLO_DISEASE_MODEL_PATH

logger = logging.getLogger(__name__)

_growth_model  = None
_disease_model = None


# ──────────────────────────────────────────────
# 모델 로더 (최초 1회만 로드, 이후 캐시 사용)
# ──────────────────────────────────────────────
def _load_growth_model():
    global _growth_model
    if _growth_model is None:
        from ultralytics import YOLO
        _growth_model = YOLO(YOLO_GROWTH_MODEL_PATH)
        logger.info(f"[YoloRunner] 생육 모델 로드 완료: {YOLO_GROWTH_MODEL_PATH}")
    return _growth_model


def _load_disease_model():
    global _disease_model
    if _disease_model is None:
        from ultralytics import YOLO
        _disease_model = YOLO(YOLO_DISEASE_MODEL_PATH)
        logger.info(f"[YoloRunner] 질병 모델 로드 완료: {YOLO_DISEASE_MODEL_PATH}")
    return _disease_model


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def _empty_result() -> dict:
    return {
        "growthResult":     "no_detection",
        "growthConfidence":  0.0,
        "diseaseResult":    "no_detection",
        "diseaseConfidence": 0.0,
    }


def _decode_image(image_bytes: bytes):
    """bytes → BGR numpy array. 실패 시 None 반환."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        logger.error("[YoloRunner] 이미지 디코딩 실패")
    return img


def _best_detection(results):
    """YOLO results에서 최고 신뢰도 클래스명과 confidence를 반환. 없으면 (None, None)."""
    boxes = results.boxes
    if boxes is None or len(boxes) == 0:
        return None, None
    best_idx   = int(boxes.conf.argmax())
    best_cls_id = int(boxes.cls[best_idx])
    best_conf  = float(boxes.conf[best_idx])
    class_name = results.names[best_cls_id]
    return class_name, round(best_conf, 4)


# ──────────────────────────────────────────────
# 생육 단계 추론
# ──────────────────────────────────────────────
def _run_growth(img):
    """
    생육 모델 추론.
    기대 클래스: "sprout", "growth", "level 1"~"level 6" (토마토), "no_detection" 등
    반환: (class_name: str, confidence: float)
    """
    try:
        model   = _load_growth_model()
        results = model(img, verbose=False)[0]
        name, conf = _best_detection(results)
        if name is None:
            logger.info("[YoloRunner][Growth] 검출 없음")
            return "no_detection", 0.0
        logger.info(f"[YoloRunner][Growth] class={name}, conf={conf}")
        return name, conf
    except Exception as e:
        logger.error(f"[YoloRunner][Growth] 추론 실패: {e}")
        return "no_detection", 0.0


# ──────────────────────────────────────────────
# 질병 탐지 추론
# ──────────────────────────────────────────────
def _run_disease(img):
    """
    질병 모델 추론.
    기대 클래스: "healthy", "disease" 또는 구체적 질병명
    반환: (class_name: str, confidence: float)
    """
    try:
        model   = _load_disease_model()
        results = model(img, verbose=False)[0]
        name, conf = _best_detection(results)
        if name is None:
            logger.info("[YoloRunner][Disease] 검출 없음")
            return "no_detection", 0.0
        logger.info(f"[YoloRunner][Disease] class={name}, conf={conf}")
        return name, conf
    except Exception as e:
        logger.error(f"[YoloRunner][Disease] 추론 실패: {e}")
        return "no_detection", 0.0


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────
def run_yolo(image_bytes: bytes, plant_stage: str = "SEED") -> dict:
    # plant_stage 파라미터 제거하고 내부에서 생육 결과로 분기
    result = _empty_result()

    img = _decode_image(image_bytes)
    if img is None:
        return result

    # 1. 생육 모델 항상 실행
    growth_name, growth_conf = _run_growth(img)
    result["growthResult"]     = growth_name
    result["growthConfidence"] = growth_conf

    # 2. SEED 판정이 아닐 때만 질병 모델 실행
    is_seed = (growth_name in ("no_detection", "Planting"))  # 모델 클래스명에 맞게 조정
    if not is_seed:
        disease_name, disease_conf = _run_disease(img)
        result["diseaseResult"]     = disease_name
        result["diseaseConfidence"] = disease_conf
    else:
        logger.info(f"[YoloRunner] growth={growth_name} → 질병 탐지 스킵")

    return result