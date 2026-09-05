"""
Fuzzy-first module/screen matcher against the source repo's file tree.

Ported from the old build's tools/architecture_resolver_fuzzy.py — logic
unchanged. This runs BEFORE any LLM call; the LLM (Agents/architecture_agent.py)
is only consulted when this comes back unconfident or ambiguous.
"""

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.55
MODULE_HIT_THRESHOLD = 0.75
AMBIGUITY_MARGIN = 0.08
CROSS_MODULE_MARGIN = 0.15

_MODULE_STOPWORDS = {"module", "the", "and"}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _normalize_module_text(text: str) -> str:
    words = re.split(r"[^a-zA-Z0-9]+", text.strip().lower())
    return "".join(w for w in words if w and w not in _MODULE_STOPWORDS)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class ResolvedSource:
    dir: str
    files: list[str]
    confidence: float
    ambiguous: bool
    resolved_by: str = "fuzzy"


def _group_by_dir(tree: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for path in tree:
        if "/" not in path:
            continue
        directory, filename = path.rsplit("/", 1)
        groups.setdefault(directory, []).append(filename)
    return groups


def _best_by_screen_name_only(tree: list[str], screen: str) -> tuple[float, str, list[str]] | None:
    screen_norm = _normalize(screen)
    scored = []
    for directory, files in _group_by_dir(tree).items():
        if not files:
            continue
        screen_folder = directory.rsplit("/", 1)[-1]
        score = _similarity(_normalize(screen_folder), screen_norm)
        scored.append((score, directory, files))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0]


def resolve_source_screen(tree: list[str], module: str, screen: str) -> ResolvedSource | None:
    module_norm = _normalize(module)
    screen_norm = _normalize(screen)

    scored: list[tuple[float, str, list[str]]] = []
    for directory, files in _group_by_dir(tree).items():
        parts = directory.split("/")
        module_hit = any(_similarity(_normalize(p), module_norm) > MODULE_HIT_THRESHOLD for p in parts)
        if not module_hit or not files:
            continue
        screen_folder = parts[-1]
        score = _similarity(_normalize(screen_folder), screen_norm)
        scored.append((score, directory, files))

    scored.sort(key=lambda t: t[0], reverse=True)
    module_scoped_best = scored[0] if scored else None

    cross_module_best = _best_by_screen_name_only(tree, screen)

    if cross_module_best and (
        module_scoped_best is None
        or cross_module_best[0] - module_scoped_best[0] >= CROSS_MODULE_MARGIN
    ):
        best_score, best_dir, best_files = cross_module_best
        if best_score < MIN_CONFIDENCE:
            return None
        logger.info(
            "resolve_source_screen: cross-module match for module=%r screen=%r -> %r "
            "(score %.3f beat module-scoped %s by >= %.2f)",
            module, screen, best_dir, best_score,
            f"{module_scoped_best[0]:.3f}" if module_scoped_best else "None",
            CROSS_MODULE_MARGIN,
        )
        return ResolvedSource(
            dir=best_dir, files=best_files, confidence=round(best_score, 3),
            ambiguous=True,
            resolved_by="fuzzy-cross-module",
        )

    if module_scoped_best is None:
        return None

    best_score, best_dir, best_files = module_scoped_best
    if best_score < MIN_CONFIDENCE:
        return None

    ambiguous = len(scored) > 1 and (best_score - scored[1][0]) < AMBIGUITY_MARGIN

    return ResolvedSource(
        dir=best_dir,
        files=best_files,
        confidence=round(best_score, 3),
        ambiguous=ambiguous,
    )


def resolve_source_module_screens(tree: list[str], module: str) -> list[ResolvedSource]:
    module_norm = _normalize(module)
    results: list[ResolvedSource] = []
    for directory, files in _group_by_dir(tree).items():
        parts = directory.split("/")
        module_hit = any(_similarity(_normalize(p), module_norm) > MODULE_HIT_THRESHOLD for p in parts)
        if not module_hit or not files:
            continue
        results.append(ResolvedSource(dir=directory, files=files, confidence=1.0, ambiguous=False))

    results.sort(key=lambda r: r.dir)
    return results


def source_from_agent_result(dirs_to_files: dict[str, list[str]], result: dict) -> ResolvedSource | None:
    directory = result.get("dir")
    confidence = result.get("confidence")

    if not directory or directory not in dirs_to_files:
        return None
    if not isinstance(confidence, (int, float)) or confidence < 0.6:
        return None

    return ResolvedSource(
        dir=directory,
        files=dirs_to_files[directory],
        confidence=round(float(confidence), 3),
        ambiguous=bool(result.get("ambiguous", False)),
        resolved_by="agent",
    )


def filter_tree_by_module(tree: list[str], module: str, min_results: int = 20) -> list[str]:
    """Cheap pre-filter applied before any repo tree ever reaches an LLM
    prompt — cuts a multi-thousand-file tree down to only paths that
    plausibly belong to the given module, so the LLM fallback (when it's
    even needed) never has to read the entire repo."""
    module_norm = _normalize_module_text(module)
    if not module_norm:
        return tree

    filtered = []
    for path in tree:
        for part in re.split(r"[/\\]", path):
            part_norm = _normalize_module_text(part)
            if not part_norm:
                continue
            if module_norm in part_norm or part_norm in module_norm:
                filtered.append(path)
                break

    if len(filtered) < min_results:
        logger.info(
            "module filter for %r only kept %d/%d path(s) -- below safety threshold, "
            "falling back to the full tree instead of risking a missed match",
            module, len(filtered), len(tree),
        )
        return tree

    logger.info("module filter for %r kept %d/%d path(s) before sending to the agent", module, len(filtered), len(tree))
    return filtered
