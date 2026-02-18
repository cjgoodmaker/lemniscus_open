"""ONNX Runtime embedder using MiniLM."""

from __future__ import annotations

import logging

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)


class Embedder:
    """Generate text embeddings using ONNX Runtime with all-MiniLM-L6-v2."""

    def __init__(
        self,
        model_path: str = "minilm.onnx",
        tokenizer_path: str = "tokenizer.json",
        max_length: int = 256,
    ) -> None:
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.max_length = max_length
        self._session: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None

    def load(self) -> None:
        if self._session is not None:
            return
        logger.info(f"Loading ONNX model from {self.model_path}")
        self._session = ort.InferenceSession(
            self.model_path,
            providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(self.tokenizer_path)
        self._tokenizer.enable_truncation(max_length=self.max_length)
        # Dynamic padding — pad to longest in batch, not fixed max_length
        self._tokenizer.enable_padding(direction="right", pad_id=0, pad_token="[PAD]")
        logger.info("Embedder ready (384-dim, ONNX Runtime)")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._session is None:
            self.load()

        if not texts:
            return []

        encoded = self._tokenizer.encode_batch(texts)

        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # Mean pooling over token embeddings, masked by attention
        token_embeddings = outputs[0]  # (batch, seq_len, hidden_size)
        mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
        summed = np.sum(token_embeddings * mask_expanded, axis=1)
        counts = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
        pooled = summed / counts

        # L2 normalize
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = np.clip(norms, a_min=1e-9, a_max=None)
        normalized = pooled / norms

        return normalized.tolist()

    def embed_single(self, text: str) -> list[float]:
        results = self.embed([text])
        return results[0] if results else []

    @property
    def vector_size(self) -> int:
        return 384
