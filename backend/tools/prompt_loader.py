from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file '{filename}' not found in {_PROMPTS_DIR}."
        )
    return path.read_text(encoding="utf-8")
