"""Helpers for the semantic Collection -> Set -> Cards planner (no network, no provider calls).

* near-duplicate detection inside a planned batch (before any image is generated),
* exclusions the Producer wrote in Free Wishes ("no buildings", "without weapons"),
* a compact, deduplicated summary of subjects planned in earlier batches of the SAME Collection + Set (+ Set type).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

HISTORY_MAX_ENTRIES = 48       # most recent distinct subjects passed to the planner
HISTORY_DESC_CHARS = 48        # short semantic hint per subject
_STOP = {"a", "an", "the", "of", "with", "and", "in", "on", "for", "to"}


def theme_key(collection: str, set_name: str, set_type: str = "standard") -> str:
    """Stable key that keeps unrelated Collections / Sets / Set types apart."""
    norm = lambda t: re.sub(r"\s+", " ", (t or "").strip().lower())  # noqa: E731
    return f"{norm(collection)}|{norm(set_name)}|{set_type}"


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word[-3] in "sxz":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_subject(name: str) -> tuple[str, ...]:
    words = [_singular(w) for w in re.findall(r"[a-z0-9]+", (name or "").lower())]
    return tuple(sorted(w for w in words if w not in _STOP))


def near_duplicates(names: Iterable[str]) -> list[tuple[str, str]]:
    """Pairs of subjects that are the same idea: identical words after normalisation (order/plural/articles ignored)
    or an almost identical spelling. 'Bicycle' and 'Bicycle Helmet' are NOT duplicates (different objects)."""
    items = [(n, normalize_subject(n)) for n in names if (n or "").strip()]
    out: list[tuple[str, str]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (a, ta), (b, tb) = items[i], items[j]
            if ta and ta == tb:
                out.append((a, b))
            elif (not any(ch.isdigit() for ch in " ".join(ta + tb))
                  and SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= 0.92):
                out.append((a, b))
    return out


_FILLER = _STOP | {"small", "large", "big", "little", "tiny", "old", "new", "classic", "vintage", "bright", "glowing", "shiny",
                   "vibrant", "colorful", "colourful", "simple", "worn", "single", "one", "its", "is", "are", "at", "by", "from",
                   "that", "this", "it", "as", "into", "over", "under", "near"}


def _concept_words(text: str) -> frozenset[str]:
    return frozenset(w for w in (_singular(x) for x in re.findall(r"[a-z]+", (text or "").lower())) if w not in _FILLER and len(w) > 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def similar_name(a: str, b: str) -> bool:
    """Same subject by name words (one contains the other's content words, or Jaccard >= 0.6): 'Brass Magnifying Glass' ~ 'Magnifying Glass'."""
    wa, wb = _concept_words(a), _concept_words(b)
    if not wa or not wb:
        return False
    return wa <= wb or wb <= wa or _jaccard(wa, wb) >= 0.6


def similar_concepts(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Pairs of (name, description) that show the same IDEA under different wording, judged on content words of the name AND the
    description (no model call). Deliberately conservative, because an unusable plan still costs a text call: it needs mostly the same
    name words plus clearly the same description, or a near-identical description. 'Bicycle' vs 'Bicycle Helmet' stays fine."""
    rows = [(n, _concept_words(n), _concept_words(d)) for n, d in items if (n or "").strip()]
    out: list[tuple[str, str]] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            (na, wa, da), (nb, wb, db) = rows[i], rows[j]
            if any(ch.isdigit() for ch in na + nb):
                continue
            name_sim, desc_sim = _jaccard(wa, wb), _jaccard(da | wa, db | wb)
            if (name_sim >= 0.6 and desc_sim >= 0.45) or (name_sim >= 0.5 and desc_sim >= 0.7):
                out.append((na, nb))
    return out


# --------------------------------------------------------------------------- Free Wishes exclusions
_EXCLUDE_RE = re.compile(r"\b(?:no|without|exclude|excluding|avoid|never|don'?t (?:include|show|use))\s+([a-z][a-z ,/&'-]{1,80})", re.I)
_SYNONYMS = {
    "building": {"house", "cabin", "hut", "cottage", "building", "lodge", "shed", "chalet"},  # unambiguous only: "barn owl" is an animal
    "house": {"house", "cabin", "hut", "cottage", "lodge", "chalet", "building"},
    "weapon": {"weapon", "sword", "gun", "rifle", "pistol", "knife", "axe", "bow", "spear", "bomb", "blaster"},
    "character": {"character", "person", "people", "man", "woman", "kid", "child", "hunter", "wizard", "knight"},
    "people": {"character", "person", "people", "man", "woman", "kid", "child"},
    "animal": {"animal", "fox", "deer", "owl", "wolf", "bear", "rabbit", "squirrel"},
}


def forbidden_terms(wishes: str) -> set[str]:
    """Words the Producer asked to avoid ('no buildings, no weapons' -> {building, house, cabin, ... weapon, ...})."""
    terms: set[str] = set()
    for m in _EXCLUDE_RE.finditer(wishes or ""):
        chunk = re.split(r"[.;!?\n]|\bbut\b|\bfocus\b", m.group(1), maxsplit=1)[0]
        for part in re.split(r",|/|&|\band\b|\bor\b", chunk):
            words = [w for w in re.findall(r"[a-z]+", part.lower()) if w not in _STOP]
            if not words or len(words) > 3:
                continue
            head = _singular(words[-1])
            terms |= _SYNONYMS.get(head, set()) | {head}
    return terms


def violates(text: str, terms: set[str]) -> str | None:
    """First forbidden term that appears as a word in `text`, else None."""
    if not terms:
        return None
    words = {_singular(w) for w in re.findall(r"[a-z]+", (text or "").lower())}
    hit = sorted(words & terms)
    return hit[0] if hit else None


# --------------------------------------------------------------------------- cross-batch history
def history_summary(entries: Iterable[tuple[str, str]], cap: int = HISTORY_MAX_ENTRIES) -> list[str]:
    """Compact, deduplicated 'name - short hint' lines, most recent first. `entries` = (name, description) newest first."""
    seen: set[tuple[str, ...]] = set()
    out: list[str] = []
    for name, desc in entries:
        key = normalize_subject(name)
        if not key or key in seen:
            continue
        seen.add(key)
        hint = re.sub(r"\s+", " ", (desc or "").strip())[:HISTORY_DESC_CHARS].rstrip(" ,;.")
        out.append(f"{name.strip()[:60]}" + (f" - {hint}" if hint else ""))
        if len(out) >= cap:
            break
    return out
