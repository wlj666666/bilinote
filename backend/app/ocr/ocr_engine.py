"""Small, lazy adapter around RapidOCR; no LLM or remote inference."""
from dataclasses import dataclass
import os
import shutil
import uuid
from pathlib import Path


@dataclass
class TextBox:
    box: tuple[float, float, float, float]  # normalized x1, y1, x2, y2
    text: str
    score: float


class OCREngine:
    def __init__(self):
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise RuntimeError("OCR 依赖未安装，请安装 backend/requirements-ocr.txt") from exc
        import cv2
        from app.ocr.runtime import requested_device, prepare_runtime, verify_runtime
        self.device = requested_device()
        prepare_runtime(self.device)
        cv2.setNumThreads(1)  # Avoid competing native thread pools with FFmpeg/ORT.
        from app.utils.path_helper import get_model_dir
        import rapidocr
        model_root = Path(get_model_dir("ocr"))
        # The pinned wheel ships its default ONNX models. Copy them locally so
        # inference never depends on a network download during a user task.
        bundled = Path(rapidocr.__file__).parent / "models"
        for name in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"):
            target = model_root / name
            if not target.exists():
                if not (bundled / name).is_file():
                    raise RuntimeError(f"OCR 模型缺失：{name}，请重新安装 requirements-ocr.txt")
                temporary = target.with_suffix(f'.{uuid.uuid4().hex}.tmp')
                shutil.copyfile(bundled / name, temporary)
                temporary.replace(target)
        params = {
            "Global.model_root_dir": str(model_root),
            "Global.log_level": "warning",
            # The upstream minimum-side rule enlarges a thin subtitle crop to
            # thousands of pixels wide. Bound detection size; recognition still
            # receives crops from the original input image.
            "Det.limit_type": "max",
            "Det.limit_side_len": 1280,
            "EngineConfig.onnxruntime.intra_op_num_threads": int(os.getenv("OCR_THREADS", "1")),
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "EngineConfig.onnxruntime.use_cuda": self.device == 'cuda',
            "EngineConfig.onnxruntime.cuda_ep_cfg.use_tf32": False,
            # Subtitle crops vary in width; avoid exhaustive cuDNN tuning for
            # every new shape and its large temporary workspace allocations.
            "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC",
            "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_use_max_workspace": False,
        }
        self.engine = RapidOCR(params=params)
        self.runtime_info = verify_runtime(self.engine, self.device)

    def read(self, image) -> list[TextBox]:
        import numpy as np
        # PyAV emits RGB; RapidOCR's ndarray input follows OpenCV's BGR convention.
        result = self.engine(np.ascontiguousarray(image[:, :, ::-1]))
        if result.boxes is None or result.txts is None:
            return []
        height, width = image.shape[:2]
        output = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            text = text.strip()
            if not text:
                continue
            output.append(TextBox(
                (float(box[:, 0].min()) / width, float(box[:, 1].min()) / height,
                 float(box[:, 0].max()) / width, float(box[:, 1].max()) / height),
                text, float(score),
            ))
        return output
