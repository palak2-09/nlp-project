"""Lazy, optional Sentence Transformers helper with an explicit offline fallback."""

from threading import Lock

_model = None
_model_name = None
_lock = Lock()


def encode_texts(model_name: str, texts: list[str]):
    global _model, _model_name
    if not texts:
        return None
    if _model is None or _model_name != model_name:
        with _lock:
            if _model is None or _model_name != model_name:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise RuntimeError("Install backend/requirements-ml.txt to enable semantic embeddings.") from exc
                _model = SentenceTransformer(model_name)
                _model_name = model_name
    return _model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
