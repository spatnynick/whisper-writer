import os
import re

import yaml
from rapidfuzz import fuzz

_GLOSSARY = None
_INITIAL_PROMPT_MAX_CHARS = 800


def _load():
    global _GLOSSARY
    if _GLOSSARY is not None:
        return _GLOSSARY

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'glossary.yaml')
    if not os.path.isfile(path):
        _GLOSSARY = {'categories': {}, 'static_map': {}, 'fuzzy': {'enabled': False}}
        return _GLOSSARY

    with open(path, 'r', encoding='utf-8-sig') as file:
        _GLOSSARY = yaml.safe_load(file) or {}
    _GLOSSARY.setdefault('categories', {})
    _GLOSSARY.setdefault('static_map', {})
    _GLOSSARY.setdefault('fuzzy', {'enabled': False})
    return _GLOSSARY


def build_initial_prompt():
    """Flatten glossary categories into a Whisper initial_prompt string, capped in length."""
    glossary = _load()
    terms = []
    for category_terms in glossary['categories'].values():
        terms.extend(category_terms)

    prompt = ', '.join(terms)
    if len(prompt) > _INITIAL_PROMPT_MAX_CHARS:
        prompt = prompt[:_INITIAL_PROMPT_MAX_CHARS].rsplit(',', 1)[0]
    return prompt


def _all_fuzzy_terms(glossary):
    terms = []
    for category_terms in glossary['categories'].values():
        terms.extend(t for t in category_terms if ' ' not in t and '/' not in t)
    return terms


def apply_glossary_corrections(text):
    """Apply static-phrase and context-gated fuzzy corrections to a transcription."""
    if not text:
        return text

    glossary = _load()

    for wrong, right in glossary['static_map'].items():
        text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)

    fuzzy_cfg = glossary.get('fuzzy', {})
    if not fuzzy_cfg.get('enabled'):
        return text

    triggers = [t.lower() for t in fuzzy_cfg.get('context_triggers', [])]
    lowered = text.lower()
    if not any(re.search(r'\b' + re.escape(trigger) + r'\b', lowered) for trigger in triggers):
        return text

    fuzzy_terms = _all_fuzzy_terms(glossary)
    if not fuzzy_terms:
        return text

    threshold = fuzzy_cfg.get('max_distance_ratio', 0.85) * 100
    trigger_set = set(triggers)

    def correct_word(match):
        word = match.group(0)
        if len(word) < 3 or word.lower() in trigger_set:
            # Trigger words are already valid vocabulary (and often short
            # prefixes of longer glossary terms, e.g. "system" / "systemd") -
            # never "correct" a word used to gate the correction in the first place.
            return word
        best_term, best_score = None, 0
        for term in fuzzy_terms:
            if term.lower() == word.lower():
                return word
            score = fuzz.ratio(word.lower(), term.lower())
            if score > best_score:
                best_term, best_score = term, score
        if best_score >= threshold:
            return best_term
        return word

    return re.sub(r"[A-Za-z']+", correct_word, text)
