import os
import re

import yaml
from rapidfuzz import fuzz

_GLOSSARY = None
_INITIAL_PROMPT_MAX_CHARS = 800

# These were the built-in English prose prompts used by earlier revisions. Keep a
# small migration check so an existing ignored config.yaml does not continue to steer
# multilingual recordings toward English after the default changes to keyword context.
_LEGACY_DEFAULT_PROMPT_PREFIXES = (
    'I am dictating natural messages about customer projects, software development, and technical support.',
    'I am dictating natural messages about customer projects, SAP consulting, ABAP programming, Linux administration, and technical support.',
)
_LANGUAGE_NEUTRAL_LOWERCASE_TERMS = {
    'systemd', 'systemctl', 'journalctl', 'hdbsql', 'saphostagent', 'sapstartsrv',
    'sapinst', 'rsyslog', 'crontab', 'kubectl', 'iptables', 'firewalld', 'selinux',
    'sudo', 'chmod', 'chown', 'rsync', 'tcpdump', 'netstat', 'fstab', 'ext4', 'xfs',
    'docker', 'kubernetes', 'pacemaker', 'corosync', 'multipath',
}


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
    """Flatten glossary categories into a language-neutral Whisper keyword prompt."""
    glossary = _load()
    terms = []
    for category_terms in glossary['categories'].values():
        for term in category_terms:
            if not isinstance(term, str) or ' ' in term:
                # Multi-word English phrases are useful for a fixed-language prompt,
                # but can steer automatic multilingual detection toward English.
                continue
            if (
                term != term.lower()
                or any(character.isdigit() for character in term)
                or any(character in term for character in '/-._')
                or term.lower() in _LANGUAGE_NEUTRAL_LOWERCASE_TERMS
            ):
                terms.append(term)

    prompt = ', '.join(terms)
    if len(prompt) > _INITIAL_PROMPT_MAX_CHARS:
        prompt = prompt[:_INITIAL_PROMPT_MAX_CHARS].rsplit(',', 1)[0]
    return prompt


def normalize_initial_prompt(prompt):
    """Return user context, replacing an old built-in English prose default.

    Whisper's prompt is context, rather than a translation instruction. A comma-separated
    glossary is safer when the input language is detected automatically because it supplies
    technical spellings without presenting a sample English sentence to continue.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return build_initial_prompt()

    normalized = ' '.join(prompt.split())
    if any(normalized.startswith(prefix) for prefix in _LEGACY_DEFAULT_PROMPT_PREFIXES):
        return build_initial_prompt()
    return prompt.strip()


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
