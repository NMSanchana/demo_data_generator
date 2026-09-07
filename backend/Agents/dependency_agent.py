import json
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set in .env")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


def _model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _load_prompt() -> str:
    return (Path(__file__).resolve().parent.parent / "prompts" / "dependency_agent_prompt.txt").read_text(encoding="utf-8")


async def _ask_llm(user_message: str) -> dict:
    client = _get_client()
    try:
        response = await client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _load_prompt()},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or "{}"
        return json.loads(text)
    except Exception as e:
        logger.warning("DependencyAgent LLM call failed: %s", e)
        return {"error": str(e)}


def _slim_fields(fields: list[dict]) -> list[dict]:
    """Only field_name + kind reach the agent -- selector/domain_sensitive
    etc. add nothing to this specific reasoning task."""
    return [{"field_name": f.get("field_name"), "kind": f.get("kind")} for f in fields]


async def detect_dependencies(screen_name: str, fields: list[dict], other_screens: list[str]) -> dict:
    """
    Ask the agent whether any of `fields` (belonging to `screen_name`)
    reference another screen's master data.

    Returns {"dependencies": [{"field_name", "depends_on_screen",
    "master_field_name"}, ...]} — always this shape, never raises. Returns
    {"dependencies": []} when there's nothing to compare against (no other
    screens in this request), when the LLM call fails, or when the model
    itself finds no plausible dependency.

    Every returned entry is sanitized against the actual inputs: a
    field_name not present in `fields`, or a depends_on_screen not present
    in `other_screens` (or equal to screen_name itself), is dropped rather
    than trusted verbatim -- a hallucinated reference must never reach the
    generation step as if it were real.
    """
    if not fields or not other_screens:
        return {"dependencies": []}

    user_message = (
        f"screen: {screen_name}\n\n"
        f"fields:\n{json.dumps(_slim_fields(fields))}\n\n"
        f"other_screens:\n{json.dumps(other_screens)}"
    )

    result = await _ask_llm(user_message)
    if "error" in result:
        logger.info("detect_dependencies: LLM call failed for screen %r -- treating as no dependencies", screen_name)
        return {"dependencies": []}

    raw_deps = result.get("dependencies")
    if not isinstance(raw_deps, list):
        return {"dependencies": []}

    valid_field_names = {f["field_name"] for f in fields if f.get("field_name")}
    other_screens_set = set(other_screens)

    cleaned: list[dict] = []
    for dep in raw_deps:
        if not isinstance(dep, dict):
            continue
        field_name = dep.get("field_name")
        depends_on_screen = dep.get("depends_on_screen")
        master_field_name = dep.get("master_field_name") or None

        if field_name not in valid_field_names:
            continue
        if depends_on_screen not in other_screens_set or depends_on_screen == screen_name:
            continue

        cleaned.append({
            "field_name": field_name,
            "depends_on_screen": depends_on_screen,
            "master_field_name": master_field_name,
        })

    if cleaned:
        logger.info(
            "detect_dependencies: screen %r has %d dependency field(s): %s",
            screen_name, len(cleaned), [(d["field_name"], d["depends_on_screen"]) for d in cleaned],
        )

    return {"dependencies": cleaned}