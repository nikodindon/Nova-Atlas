"""Bounded admin configuration editing; no environment credential management here."""
import html
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit, parse_qsl

import yaml

CONFIG_LOCK = threading.RLock()
SECRET_KEY = re.compile(r'password|passwd|secret|token|credential|api[_-]?key|authorization', re.I)
MASKS = {'', '***', '********', '••••••••', '[REDACTED]'}


def confined_file(base, relative):
    base = Path(base).resolve()
    result = (base / relative).resolve()
    if not result.is_relative_to(base):
        raise ValueError('Chemin hors répertoire autorisé')
    return result


def credential_url(value):
    try:
        parsed = urlsplit(value)
        return bool(parsed.username or parsed.password or
                    any(SECRET_KEY.search(k) for k, v in parse_qsl(parsed.query)))
    except ValueError:
        return True


def public_feeds(feeds):
    # Credential-bearing historical URLs are editable only locally; never reflect them.
    return {cat: [url if not credential_url(url) else '[URL privée masquée]'
                  for url in urls] for cat, urls in feeds.items()}


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_view(value, key='', *, escape_html=True):
    if SECRET_KEY.search(key):
        return ''
    if isinstance(value, dict):
        return {k: safe_view(v, str(k), escape_html=escape_html) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_view(v, key, escape_html=escape_html) for v in value]
    if isinstance(value, str):
        if '://' in value:
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                return ''  # Do not echo credential-bearing URLs or allow accidental clearing.
        return html.escape(value, quote=True) if escape_html else value
    return value


TEXT_FIELDS = {
    ('service', 'name'): 100, ('service', 'tagline'): 300,
    ('service', 'default_language'): 20,
    ('llm', 'provider'): 20, ('llm', 'model'): 512, ('llm', 'base_url'): 2048,
    ('radio', 'bitrate'): 10, ('icecast', 'host'): 253, ('icecast', 'mount'): 100,
    ('icecast', 'password'): 1024,
}
NUM_FIELDS = {
    ('llm', 'timeout_fetch'): (1, 3600), ('llm', 'timeout_report'): (1, 7200),
    ('llm', 'timeout_edition'): (1, 7200), ('llm', 'timeout_bulletin'): (1, 7200),
    ('rss', 'max_articles_per_feed'): (1, 100), ('rss', 'live_window_hours'): (1, 168),
    ('web', 'port'): (1024, 65535), ('icecast', 'port'): (1, 65535),
    ('posts', 'nb_posts_per_cycle'): (1, 20), ('posts', 'window_hours'): (1, 168),
}


def validate_update(section, key, value):
    field = (section, key)
    if field in TEXT_FIELDS:
        if not isinstance(value, str) or len(value) > TEXT_FIELDS[field] or any(c in value for c in '\r\n\x00<>'):
            raise ValueError('Valeur texte invalide')
        if field == ('llm', 'provider') and value not in ('ollama', 'llama-server'):
            raise ValueError('Provider invalide')
        if field == ('service', 'default_language') and not re.fullmatch('[A-Za-z_-]{2,20}', value):
            raise ValueError('Langue invalide')
        if field == ('llm', 'base_url') and value:
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('URL sans credentials requise')
        if field == ('icecast', 'host') and not re.fullmatch(r'[A-Za-z0-9.:-]+', value):
            raise ValueError('Hôte invalide')
        if field == ('icecast', 'mount') and not re.fullmatch(r'/[A-Za-z0-9_./-]+', value):
            raise ValueError('Mount invalide')
        if field == ('radio', 'bitrate') and value not in ('64k', '96k', '128k', '192k', '256k', '320k'):
            raise ValueError('Bitrate invalide')
    elif field in NUM_FIELDS:
        low, high = NUM_FIELDS[field]
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Valeur numérique hors limites')
    elif field == ('radio', 'background_volume'):
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError('Volume hors limites')
    elif field == ('radio', 'voices'):
        if not isinstance(value, dict) or len(value) > 20:
            raise ValueError('Voix invalides')
        for lang, voices in value.items():
            if not re.fullmatch('[a-z]{2}', lang) or not isinstance(voices, list) or len(voices) > 30:
                raise ValueError('Voix invalides')
            if any(not isinstance(v, str) or not re.fullmatch('[A-Za-z0-9-]{5,100}', v) for v in voices):
                raise ValueError('Voix invalides')
    else:
        raise ValueError('Champ non modifiable par le web')


def update_yaml(path, section, key, value):
    if os.environ.get('ATLAS_CONTAINER') == '1' and (section == 'icecast' or (section,key) == ('web','port')):
        raise ValueError('Container-managed setting; update deployment configuration/secrets offline')
    if not isinstance(section, str) or not isinstance(key, str):
        raise ValueError('Section et clé requises')
    validate_update(section, key, value)
    with CONFIG_LOCK:
        cfg = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(cfg, dict) or not isinstance(cfg.get(section, {}), dict):
            raise ValueError('Structure YAML invalide')
        # Empty secret inputs mean "keep existing". A mask must never become a credential.
        if (SECRET_KEY.search(key) or key == 'base_url') and value in MASKS:
            return
        cfg.setdefault(section, {})[key] = value
        atomic_text(path, yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))


def simple_update(paths, body, loader, saver):
    # The form helper adds this hidden field before legacy FormData serialization.
    # Auth/CSRF have already been checked by the request hook.
    body = {key: value for key, value in body.items() if key != 'csrf_token'}
    specs = {'fetch_timeout': (1, 3600), 'edition_timeout': (1, 7200),
             'max_articles_feed': (1, 100), 'live_window_hours': (1, 168)}
    if not body or set(body) - (set(specs) | {'ollama_model'}):
        raise ValueError('Champ non modifiable')
    for key, value in body.items():
        if key == 'ollama_model':
            validate_update('llm', 'model', value)
        elif type(value) is not int or not specs[key][0] <= value <= specs[key][1]:
            raise ValueError('Valeur numérique hors limites')
    with CONFIG_LOCK:
        cfg = loader(paths)
        cfg.update(body)
        saver(paths, cfg)


def read_config(path):
    # No loader diagnostics containing YAML snippets/credentials in logs.
    from copy import deepcopy
    from modules.core.config import DEFAULT_CONFIG
    result = deepcopy(DEFAULT_CONFIG)
    path = Path(path)
    if not path.exists():
        return result
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(cfg, dict):
        raise ValueError('Structure YAML invalide')
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    return result
