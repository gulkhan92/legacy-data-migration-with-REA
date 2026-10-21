"""Local embeddings with BAAI/bge-small-en-v1.5 (384 dimensions, CPU).

The model is loaded once per process (a worker loads it at start) and every call encodes a batch. Vectors are
normalised, so cosine similarity is a dot product and pgvector's cosine distance is `1 - similarity`.
In a container the model is baked into the image (see `python -m app.mapping.download_model` and backend/Dockerfile),
so no download happens at run time.
"""

import threading
from pathlib import Path

import numpy as np

from app.core.config import get_settings

DIMENSIONS = 384
BATCH_SIZE = 32


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingService:
    def __init__(self, model_name: str | None = None, cache_dir: str | None = None, batch_size: int = BATCH_SIZE) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model
        self.cache_dir = cache_dir or settings.embedding_cache_dir
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()
        self.loads = 0  # how many times the model was loaded in this process (1 after the first use)

    def _load(self):  # type: ignore[no-untyped-def]
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer

                    Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
                    self._model = SentenceTransformer(self.model_name, device="cpu", cache_folder=self.cache_dir)
                except Exception as exc:  # pragma: no cover - depends on the environment
                    raise EmbeddingUnavailable(f"The embedding model {self.model_name} could not be loaded: {exc}") from exc
                self.loads += 1
        return self._model

    def encode(self, texts: list[str]) -> np.ndarray:
        """Normalised float32 vectors, one row per text, encoded in batches."""
        if not texts:
            return np.zeros((0, DIMENSIONS), dtype=np.float32)
        model = self._load()
        vectors = model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return vectors.astype(np.float32)


_service: EmbeddingService | None = None
_service_lock = threading.Lock()


def get_embedding_service() -> EmbeddingService:
    """The process wide service: the model is loaded once, on first use."""
    global _service
    with _service_lock:
        if _service is None:
            _service = EmbeddingService()
        return _service
