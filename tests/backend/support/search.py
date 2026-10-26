"""Explicit test-only model injection; production API/repository stay unchanged.

This module is mounted only into the isolated integration stack. No environment
switch in the production application enables these deterministic embeddings.
"""
import numpy as np
from app.api import create_app
from app.config import Settings


class FixtureEmbedder:
    model_name = "intfloat/multilingual-e5-small"
    revision = "test-fixture-v1"

    def load(self):
        pass

    def encode_query(self, query):
        vector = np.zeros(384, dtype=np.float32)
        vector[0] = 1.0
        return vector


app = create_app(settings=Settings.from_env(), embedder=FixtureEmbedder())
