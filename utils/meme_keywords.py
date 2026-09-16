# -*- coding: utf-8 -*-
"""Helpers for normalizing streamer meme keywords."""


def extract_keyword(value):
    """Return a plain keyword string from a string or meme-result object."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if value.get("recommend") is False:
            return ""
        return str(value.get("keyword") or "").strip()
    return ""


def normalize_keywords(values):
    """Normalize keyword values to a de-duplicated list of strings."""
    result = []
    seen = set()
    for value in values or []:
        keyword = extract_keyword(value)
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
    return result


def merge_keywords(*groups):
    """Merge multiple keyword lists while preserving first-seen order."""
    merged = []
    seen = set()
    for group in groups:
        for keyword in normalize_keywords(group):
            if keyword in seen:
                continue
            seen.add(keyword)
            merged.append(keyword)
    return merged
