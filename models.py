import asyncio
import os
from functools import lru_cache

from openai import AsyncOpenAI

DEFAULT_CHAT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
DIMENSION = 384
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def chat_client(timeout=10):
    model = os.getenv("CHAT_MODEL") or DEFAULT_CHAT_MODEL
    if model != "openrouter/free" and not model.endswith(":free"):
        raise ValueError("CHAT_MODEL must use OpenRouter's free tier")
    return AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        timeout=timeout,
        max_retries=0,
    )


@lru_cache(maxsize=1)
def encoder():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL, cache_dir=os.getenv("EMBEDDING_CACHE", "data/models"), threads=2)


def encode(inputs):
    return [vector.tolist() for vector in encoder().embed(inputs, batch_size=32)]


async def embed(inputs):
    vectors = await asyncio.to_thread(encode, inputs)
    if len(vectors) != len(inputs) or any(len(vector) != DIMENSION for vector in vectors):
        raise RuntimeError("Unexpected embedding shape")
    return vectors
