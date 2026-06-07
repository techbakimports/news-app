"""
Histórico de itens já postados — evita repetição entre execuções do pipeline.

Armazena em logs/posted_history.json com TTL configurável (padrão 48h).
Usa interseção de palavras significativas para detectar tópicos similares,
o mesmo critério do dedup interno de cada pipeline.
"""
from __future__ import annotations

import json
import os
import time

_HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "posted_history.json"
)
_TTL_HOURS = 48   # horas que um título fica bloqueado
_THRESHOLD = 0.5  # % de palavras em comum para considerar duplicata


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------

def _title_words(title: str) -> set[str]:
    """Palavras significativas (> 4 chars) do título em minúsculo."""
    return {w for w in title.lower().split() if len(w) > 4}


def _load() -> list[dict]:
    """Carrega histórico e descarta entradas expiradas."""
    if not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    cutoff = time.time() - _TTL_HOURS * 3600
    return [e for e in data if e.get("ts", 0) > cutoff]


def _save(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _is_duplicate(title: str, entries: list[dict], threshold: float = _THRESHOLD) -> bool:
    words = _title_words(title)
    if not words:
        return False
    for e in entries:
        hist_words = _title_words(e.get("title", ""))
        if not hist_words:
            continue
        overlap = len(words & hist_words) / max(1, min(len(words), len(hist_words)))
        if overlap >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def filter_not_posted(items: list[dict], threshold: float = _THRESHOLD) -> tuple[list[dict], int]:
    """
    Remove da lista itens com título similar a algo já postado nas últimas 48h.

    Retorna (itens_novos, quantidade_removida).
    """
    entries = _load()
    result = []
    removed = 0
    for item in items:
        if _is_duplicate(item.get("title", ""), entries, threshold):
            removed += 1
        else:
            result.append(item)
    return result, removed


def mark_as_posted(title: str, pipeline: str = "") -> None:
    """Registra um título como postado para bloquear repetições futuras."""
    entries = _load()
    entries.append({
        "title":    title,
        "pipeline": pipeline,
        "ts":       time.time(),
    })
    _save(entries)


def stats() -> dict:
    """Retorna estatísticas do histórico atual (para debug/log)."""
    entries = _load()
    by_pipeline: dict[str, int] = {}
    for e in entries:
        p = e.get("pipeline", "?")
        by_pipeline[p] = by_pipeline.get(p, 0) + 1
    return {"total": len(entries), "by_pipeline": by_pipeline, "ttl_hours": _TTL_HOURS}
