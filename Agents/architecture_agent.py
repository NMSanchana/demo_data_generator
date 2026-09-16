import json
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI
from dotenv import load_dotenv

from service.gitlab_service import get_gitlab_service
from tools import architecture_resolver_fuzzy as fuzzy

load_dotenv()

logger = logging.getLogger(__name__)

FUZZY_CONFIDENCE_THRESHOLD = 0.75

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
    return (Path(__file__).resolve().parent.parent / "prompts" / "architecture_prompt.txt").read_text(encoding="utf-8")


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
        logger.warning("ArchitectureAgent LLM call failed: %s", e)
        return {"error": str(e)}


def _pick_html_path(files: list[str], directory: str) -> str | None:
    """Given a resolved screen folder's filenames, prefer a .component.html
    file; fall back to any .html file in that folder."""
    component_html = [f for f in files if f.endswith(".component.html")]
    if component_html:
        return f"{directory}/{component_html[0]}"
    any_html = [f for f in files if f.endswith(".html")]
    if any_html:
        return f"{directory}/{any_html[0]}"
    return None


async def _llm_resolve_screen(tree: list[str], module: str, screen: str) -> fuzzy.ResolvedSource | None:
    filtered_tree = fuzzy.filter_tree_by_module(tree, module)
    user_message = (
        f"scope: screen\nmodule: {module}\nscreen: {screen}\n\n"
        f"repo file paths:\n" + "\n".join(filtered_tree)
    )
    result = await _ask_llm(user_message)
    if "error" in result:
        return None

    # Two response shapes are tolerated: {"path": "..."} (matches the
    # existing prompt file) or {"dir": "...", "confidence": ...} (matches
    # the old build's agent). Normalize both to a ResolvedSource.
    dirs_to_files = fuzzy._group_by_dir(tree)

    if "dir" in result:
        return fuzzy.source_from_agent_result(dirs_to_files, result)

    path = result.get("path")
    if not path or "/" not in path:
        return None
    directory, filename = path.rsplit("/", 1)
    files = dirs_to_files.get(directory)
    if files is None or filename not in files:
        return None
    confidence = result.get("confidence", 0.6)
    if not isinstance(confidence, (int, float)) or confidence < 0.6:
        return None
    return fuzzy.ResolvedSource(
        dir=directory, files=files, confidence=round(float(confidence), 3),
        ambiguous=bool(result.get("ambiguous", False)), resolved_by="agent",
    )


async def _llm_resolve_module_screens(tree: list[str], module: str) -> list[fuzzy.ResolvedSource]:
    filtered_tree = fuzzy.filter_tree_by_module(tree, module)
    user_message = (
        f"scope: module\nmodule: {module}\n\n"
        f"repo file paths:\n" + "\n".join(filtered_tree)
    )
    result = await _ask_llm(user_message)
    if "error" in result or not isinstance(result.get("screens"), list) or not result["screens"]:
        return []

    dirs_to_files = fuzzy._group_by_dir(tree)
    resolved_list: list[fuzzy.ResolvedSource] = []
    for item in result["screens"]:
        resolved = None
        if "dir" in item:
            resolved = fuzzy.source_from_agent_result(dirs_to_files, item)
        elif "path" in item and "/" in item["path"]:
            directory, filename = item["path"].rsplit("/", 1)
            files = dirs_to_files.get(directory)
            if files is not None and filename in files:
                confidence = item.get("confidence", 0.6)
                resolved = fuzzy.ResolvedSource(
                    dir=directory, files=files, confidence=round(float(confidence), 3),
                    ambiguous=bool(item.get("ambiguous", False)), resolved_by="agent",
                )
        if resolved is None:
            return []
        resolved_list.append(resolved)

    resolved_list.sort(key=lambda r: r.dir)
    return resolved_list


async def fetch_component_html(path: str) -> str | None:
    service = get_gitlab_service()
    content = service.fetch_file(path)
    if content is None:
        logger.warning("ArchitectureAgent: file not found in repo: %s", path)
    return content


async def resolve_screen(module: str, screen: str) -> dict:
    """
    Resolve one module+screen to its component HTML path and content.

    Returns:
      {"ok": True, "screen_name": str, "dir": str, "path": str,
       "component_html": str, "confidence": float, "ambiguous": bool,
       "resolved_by": str}
      or {"ok": False, "screen_name": screen, "error": str}
    """
    service = get_gitlab_service()
    tree = service.get_repo_tree()

    resolved = fuzzy.resolve_source_screen(tree, module, screen)
    if resolved is None or resolved.confidence < FUZZY_CONFIDENCE_THRESHOLD or resolved.ambiguous:
        llm_resolved = await _llm_resolve_screen(tree, module, screen)
        if llm_resolved is not None:
            resolved = llm_resolved
        logger.info(
            "resolve_screen: fuzzy result for %s/%s was %s -- %s",
            module, screen,
            "unconfident/ambiguous" if resolved is None else f"confidence={resolved.confidence}",
            "used LLM fallback" if llm_resolved is not None else "kept fuzzy result (LLM fallback unusable)",
        )

    if resolved is None:
        return {"ok": False, "screen_name": screen, "error": f"Could not resolve module '{module}' / screen '{screen}' against the source repository."}

    html_path = _pick_html_path(resolved.files, resolved.dir)
    if html_path is None:
        return {"ok": False, "screen_name": screen, "error": f"Resolved folder '{resolved.dir}' contains no HTML file."}

    html = await fetch_component_html(html_path)
    if html is None:
        return {"ok": False, "screen_name": screen, "error": f"File not found in repo: {html_path}"}

    return {
        "ok": True,
        "screen_name": screen,
        "dir": resolved.dir,
        "path": html_path,
        "component_html": html,
        "confidence": resolved.confidence,
        "ambiguous": resolved.ambiguous,
        "resolved_by": resolved.resolved_by,
    }


async def resolve_module_screens(module: str) -> list[dict]:
    """
    Resolve every real screen folder under a module (blank Screen ->
    full-module generation). Returns a list of per-screen dicts in the
    same shape as resolve_screen() (screen_name is derived from the
    folder name).
    """
    service = get_gitlab_service()
    tree = service.get_repo_tree()

    resolved_list = fuzzy.resolve_source_module_screens(tree, module)
    if not resolved_list:
        resolved_list = await _llm_resolve_module_screens(tree, module)

    if not resolved_list:
        return []

    results: list[dict] = []
    for resolved in resolved_list:
        screen_name = resolved.dir.rsplit("/", 1)[-1]
        html_path = _pick_html_path(resolved.files, resolved.dir)
        if html_path is None:
            results.append({"ok": False, "screen_name": screen_name, "error": f"Resolved folder '{resolved.dir}' contains no HTML file."})
            continue
        html = await fetch_component_html(html_path)
        if html is None:
            results.append({"ok": False, "screen_name": screen_name, "error": f"File not found in repo: {html_path}"})
            continue
        results.append({
            "ok": True,
            "screen_name": screen_name,
            "dir": resolved.dir,
            "path": html_path,
            "component_html": html,
            "confidence": resolved.confidence,
            "ambiguous": resolved.ambiguous,
            "resolved_by": resolved.resolved_by,
        })

    return results
