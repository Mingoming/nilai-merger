import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations


def clean_filename(name: str) -> str:
    """Normalisasi nama export tanpa fuzzy matching."""
    cleaned = str(name).strip()
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    cleaned = re.sub(r"\s*P\.?\s*[12]\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(?i)(-grades)\s*(?:\(\d+\)|\d+)\s*$",
        r"\1",
        cleaned,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    return cleaned


def filename_key(name: str) -> str:
    cleaned = clean_filename(name)
    base = re.sub(r"(?i)-grades$", "", cleaned).strip()
    return re.sub(r"\s+", " ", base).strip().casefold()


def safe_output_name(name: str) -> str:
    # Aman dipakai sebagai nama file di Windows/Linux.
    value = re.sub(r'[<>:"/\\|?*]+', "_", clean_filename(name)).strip(" .")
    return value or "hasil"


@dataclass(frozen=True)
class SimilarCandidate:
    left: str
    right: str
    score: float


def similar_group_candidates(keys) -> list[SimilarCandidate]:
    """Suggestions only; never feed these names into exact filename_key()."""
    prepared = {}
    for key in sorted(set(keys)):
        tokens = re.findall(r"\w+", key.casefold())
        # Expand the common B. abbreviation for suggestions only. LANJUT stays.
        tokens = ["bahasa" if token == "b" else token for token in tokens]
        levels = {t for t in tokens if t in {"x", "xi", "xii", "10", "11", "12"}}
        subject = set(tokens) - levels - {"to", "ujian", "kelas", "nilai", "grades"}
        prepared[key] = (" ".join(tokens), levels, subject)
    candidates = []
    # ponytail: quadratic scan, bounded by ZIP limits; index shared tokens if
    # real batches make analysis slow. Only the best five per group are shown.
    for left, right in combinations(prepared, 2):
        a, a_levels, a_subject = prepared[left]
        b, b_levels, b_subject = prepared[right]
        if a_levels != b_levels or not a_subject.intersection(b_subject):
            continue
        # 0.78 admits common abbreviations/typos but requires shared subject
        # vocabulary and matching class levels to filter irrelevant pairs.
        score = SequenceMatcher(None, a, b, autojunk=False).ratio()
        if score >= 0.78:
            candidates.append(SimilarCandidate(left, right, round(score, 4)))
    counts = dict.fromkeys(prepared, 0)
    selected = []
    for candidate in sorted(candidates, key=lambda c: (-c.score, c.left, c.right)):
        if counts[candidate.left] >= 5 or counts[candidate.right] >= 5:
            continue
        selected.append(candidate)
        counts[candidate.left] += 1
        counts[candidate.right] += 1
    return sorted(selected, key=lambda c: (c.left, c.right))
