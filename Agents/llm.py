"""Shared OpenAI client and model name for every agent.

The key is read from OPENAI_API_KEY and the model from OPENAI_MODEL (both in
.env). Agents import from here instead of each keeping their own copy.
"""
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

DEFAULT_MODEL = "gpt-4o-mini"

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set in .env")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


def get_model() -> str:
    return os.getenv("OPENAI_MODEL", DEFAULT_MODEL)