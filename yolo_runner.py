import logging
import numpy as np
import cv2
from config import YOLO_GROWTH_MODEL_PATH, YOLO_DISEASE_MODEL_PATH

logger = logging.getLogger(__name__)

_growth_model  = None
_disease_model = None


# ──────────────────────────────────────────────
# 생육 클래스명 → 품종 단계 인덱스 매핑
# ──────────────────────────────────────────────
# 규칙: 씨앗=0 (모든 작물 공통, YOLO는 씨앗을 탐지하지 않음)
#       정식=1 / 생육=2 / 수확=3 ...
# BE로는 "이름"이 아니라 이 "숫자 인덱스"를 보낸다. (관리자가 FE에서 단계 이름을
# 바꿔도 안 깨지고, 재학습으로 단계가 늘어도 BE 수정 없이 유연하게 대응)
# ▶ 재학습/작물 변경(예: 딸기 6단계)으로 클래스명이 바뀌면 이 표만 갱신하면 된다.
GROWTH_STAGE_INDEX = {
    # 상추 생육 모델
    "Planting": 1, "정식기": 1, "sprout": 1,
    "Growing":  2, "생육기": 2, "growth": 2,
    "Harvest":  3, "수확기": 3, "harvest": 3,
    # 예) 딸기/토마토 재학습 시 여기에 추가:
    # "flowering": 4, "fruiting": 5, "ripening": 6,
}


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


def _to_stage_index(growth_name):
    """생육 클래스명을 품종 단계 인덱스(숫자)로 변환. 알 수 없으면 None.
    - 매핑표에 있으면 그 값
    - 클래스명이 이미 숫자면 그 숫자(향후 숫자 클래스 모델도 그대로 지원)
    """
    if growth_name is None:
        return None
    if growth_name in GROWTH_STAGE_INDEX:
        return GROWTH_STAGE_INDEX[growth_name]
    trimmed = str(growth_name).strip()
    if trimmed.isdigit():
        return int(trimmed)
    return None


# ──────────────────────────────────────────────
# 생육 단계 추론
# ──────────────────────────────────────────────
def _run_growth(img):
    """
    생육 모델 추론.
    기대 클래스: "Planting", "Growing", "Harvest" 등 (작물별 상이)
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

    # 1. 생육 모델 실행 → 클래스명을 품종 단계 인덱스(숫자)로 변환해 전송한다.
    #    BE는 이 숫자를 그대로 stage_index로 사용한다(이름 대조 없음).
    growth_name, growth_conf = _run_growth(img)
    stage_index = _to_stage_index(growth_name)

    if stage_index is None:
        # 미검출 또는 매핑표에 없는 클래스 → 단계 변경 없이 no_detection 전송
        result["growthResult"]     = "no_detection"
        result["growthConfidence"] = 0.0
    else:
        result["growthResult"]     = str(stage_index)   # 예: "2"
        result["growthConfidence"] = growth_conf

    # 2. 초기 단계(미검출 또는 정식=1)에는 질병 모델 스킵. 생육(2) 이상만 질병 탐지.
    is_early = (stage_index is None or stage_index <= 1)
    if not is_early:
        disease_name, disease_conf = _run_disease(img)
        result["diseaseResult"]     = disease_name
        result["diseaseConfidence"] = disease_conf
    else:
        logger.info(f"[YoloRunner] stage={result['growthResult']} → 질병 탐지 스킵")

    return result
