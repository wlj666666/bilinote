"""Keep the small search embedding model inside the CPU resource budget."""
from functools import cached_property
from pathlib import Path
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
from app.services.task_limits import positive_env


class CPUEmbeddingFunction(ONNXMiniLM_L6_V2):
    def __init__(self):
        super().__init__(preferred_providers=['CPUExecutionProvider'])

    @staticmethod
    def name():
        # Same MiniLM weights/dimensions as Chroma's historical default. Only the
        # runtime provider/thread budget changes; existing indexes stay usable.
        return 'default'

    def get_config(self):
        return {}

    @cached_property
    def model(self):
        options = self.ort.SessionOptions()
        options.intra_op_num_threads = positive_env('EMBEDDING_CPU_THREADS', 2)
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        options.graph_optimization_level = self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        return self.ort.InferenceSession(
            str(Path(self.DOWNLOAD_PATH) / self.EXTRACTED_FOLDER_NAME / 'model.onnx'),
            providers=['CPUExecutionProvider'], sess_options=options)
