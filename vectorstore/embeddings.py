"""
A small, dependency-free embedding function for Chroma.

Chroma's default embedding function downloads an ONNX MiniLM model from the
internet on first use, which this sandboxed environment's network allowlist
blocks. This hashing-trick embedder (bag of word/bigram hashes, signed and
L2-normalized) needs no network or model weights, so the vector store works
completely offline. It's a stand-in for semantic quality only -- swap in a
real embedding model (OpenAI, Voyage, a locally cached sentence-transformer)
by replacing get_embedding_function() once network/model access is available.
"""
import hashlib
import re

import numpy as np
from chromadb.api.types import EmbeddingFunction


class HashingEmbeddingFunction(EmbeddingFunction):
    def __init__(self, dim: int = 384):
        self.dim = dim

    def name(self):
        return "local-hashing-embedder"

    def get_config(self):
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config):
        return HashingEmbeddingFunction(dim=config.get("dim", 384))

    def __call__(self, input):
        vectors = []
        for text in input:
            vec = np.zeros(self.dim, dtype=np.float32)
            tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
            for i in range(len(tokens)):
                for n in (1, 2):
                    gram = " ".join(tokens[i : i + n])
                    if not gram:
                        continue
                    digest = int(hashlib.md5(gram.encode()).hexdigest(), 16)
                    idx = digest % self.dim
                    sign = 1.0 if (digest // self.dim) % 2 == 0 else -1.0
                    vec[idx] += sign
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            vectors.append(vec.tolist())
        return vectors


def get_embedding_function():
    return HashingEmbeddingFunction()
