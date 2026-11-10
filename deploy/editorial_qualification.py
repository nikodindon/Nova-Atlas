"""Read-only editorial evidence export. No HTTP, model, service control or upload."""
import argparse
import ast
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit, urlunsplit

from deploy.recovery import checked, publish, sync

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
FIELDS = ('title', 'title_original', 'category', 'source', 'link', 'pub_date', 'timestamp', 'hash')
SECRET = re.compile(r'(?i)(?:bearer\s+\S+|(?:sk-or-v1-|sk-proj-|hf_)[a-z0-9_-]{12,}|(?:password|api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+)')
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}')


class ExportError(ValueError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=MAX_FILE):
    path = checked(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ExportError('unsafe_or_large_input')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ExportError('input_changed_retry_export')
    return raw


def decode(raw):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ExportError('invalid_json_preserved') from None


def text(value, limit=20000):
    if not isinstance(value, str) or len(value) > limit:
        raise ExportError('invalid_text')
    return value


def redact(value, public=False):
    value = SECRET.sub('[REDACTED]', value)
    value = re.sub(r'(?is)<think\b[^>]*>.*?(?:</think>|\Z)', '[REASONING OMITTED]', value)
    value = re.sub(r'(?is)<\|channel>thought.*?(?:<channel\|>|\Z)', '[REASONING OMITTED]', value)
    if public:
        value = EMAIL.sub('[EMAIL OMITTED]', value)
    value = re.sub(r'https?://[^\s<>]+', lambda m: safe_url(m[0], public=public) or '[URL OMITTED]', value)
    return value


def safe_url(value, public=False):
    try:
        u = urlsplit(value)
        if (u.scheme not in ('http', 'https') or not u.hostname or u.username is not None
                or u.password is not None or any(c.isspace() or ord(c) < 32 for c in value)
                or '\\' in value):
            return None
        if u.port is not None and not 1 <= u.port <= 65535:
            return None
        return urlunsplit((u.scheme, u.netloc, u.path, '' if public else u.query, '' if public else u.fragment))
    except (ValueError, TypeError):
        return None


def output_bundle(destination, values, forbidden=()):
    """New directory only, never under source state or a Git worktree."""
    destination = checked(destination)
    for base in forbidden:
        base = checked(base)
        if destination == base or destination.is_relative_to(base) or base.is_relative_to(destination):
            raise ExportError('output_must_be_disjoint')
    if any((p / '.git').is_file() or (p / '.git/HEAD').is_file() for p in (destination, *destination.parents)):
        raise ExportError('private_output_outside_git_required')
    parent = checked(destination.parent)
    if not parent.is_dir() or destination.exists():
        raise ExportError('new_output_directory_required')
    with tempfile.TemporaryDirectory(prefix='.editorial-', dir=parent) as stage:
        stage = Path(stage)
        for name, content in values.items():
            if not re.fullmatch(r'[a-z-]+\.(json|md)', name):
                raise ExportError('invalid_output_name')
            path = stage / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        sync(stage)
        publish(stage, destination)  # renameat2 NOREPLACE, same filesystem
        sync(parent)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def markdown(report, public=False):
    lines = ['# Nova Atlas — qualification éditoriale', '',
        'Brouillon local ; aucune autorisation de publication implicite.', '',
        'Les sorties stockées peuvent avoir été nettoyées après génération.',
        'Fidélité factuelle : non évaluée sans référence source et revue humaine.', '']
    for row in report['examples']:
        lines += ['## ' + row['id'], '']
        # Indented code blocks keep HTML, Markdown and links from becoming active.
        for key, value in row.items():
            if value is not None:
                for line in (key + ': ' + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))).splitlines():
                    lines.append('    ' + line)
        lines.append('')
    lines += ['## Scripts radio', '', '    ' + json.dumps(report['radio'], ensure_ascii=False), '']
    return '\n'.join(lines)


def prompt_reference(repo):
    raw = read(checked(repo) / 'modules/fetch/atlas_fetch.py')
    source = raw.decode('utf-8')
    tree = ast.parse(source)
    node = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_summarize'), None)
    return {'status': 'current_checkout_candidate_not_historical_proof',
        'file_sha256': sha(raw),
        'function_sha256': sha(ast.get_source_segment(source, node).encode()) if node else None,
        'exact_historical_prompt': None, 'historical_source_input': None}


def observed_model(state):
    import yaml
    try:
        cfg = yaml.safe_load(read(state / 'config/config.yaml', 256 * 1024))
    except yaml.YAMLError:
        raise ExportError('invalid_config') from None
    if not isinstance(cfg, dict) or not isinstance(cfg.get('llm', {}), dict):
        raise ExportError('invalid_config')
    llm = cfg.get('llm', {})
    model = llm.get('model')
    model = Path(model).name if isinstance(model, str) else None
    return {'status': 'observed_configuration_not_generation_provenance',
        'provider': llm.get('provider') if llm.get('provider') in ('llama-server', 'ollama', 'litellm') else 'unknown',
        'model_basename': redact(model) if model else None,
        'parameters': {k: llm[k] for k in ('temperature', 'max_tokens', 'timeout_fetch') if type(llm.get(k)) in (int, float)},
        'duration_ms': None, 'prompt_tokens': None, 'completion_tokens': None, 'finish_reason': None}


def radio_inventory(state):
    cache = state / 'data/llm_cache'
    tagged = 0
    size = 0
    paths = sorted(cache.glob('*.txt')) if cache.exists() else []
    if len(paths) > 2000:
        raise ExportError('cache_inventory_limit')
    for path in paths:
        raw = read(path, 256 * 1024)
        size += len(raw)
        if size > MAX_TOTAL:
            raise ExportError('cache_byte_limit')
        if re.search(rb'\[VOIX\d+\]', raw):
            tagged += 1
    # A cache hit without an attributable job is not a complete archival record.
    mp3 = list((state / 'audio_queue').glob('*.mp3')) + list((state / 'audio_queue/android_cache').glob('*.mp3'))
    return {'scripts_recovered': 0, 'exact_scripts': [], 'voice_tagged_cache_candidates': tagged,
        'mp3_files_observed': len(mp3), 'status': 'no_attributable_script_archive',
        'audio_transcription_performed': False,
        'limitation': 'MP3 and receipts do not recover the exact script, prompt or speaker tags.'}


def export(state, repo, destination, count=20):
    if type(count) is not int or not 15 <= count <= 20:
        raise ExportError('sample_size_15_to_20')
    state, repo = checked(state), checked(repo)
    files = sorted((state / 'data/articles').glob('*_articles.json'), reverse=True)
    if not files or len(files) > 366:
        raise ExportError('article_inventory_limit_or_empty')
    groups = defaultdict(deque)
    seen = set()
    total = 0
    inputs = []
    skipped = 0
    for path in files:
        raw = read(path)
        total += len(raw)
        if total > MAX_TOTAL:
            raise ExportError('article_byte_limit')
        rows = decode(raw)
        if not isinstance(rows, list) or len(rows) > 20000:
            raise ExportError('invalid_article_file')
        inputs.append({'name': path.name, 'sha256': sha(raw), 'records': len(rows)})
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            try:
                summary = text(row.get('summary', ''))
                fields = {k: text(row.get(k, '')) for k in FIELDS}
                if not summary.strip() or not safe_url(fields['link']):
                    raise ExportError('invalid_article')
            except ExportError:
                skipped += 1
                continue
            identity = sha((fields['hash'] or fields['link']).encode())
            if identity in seen:
                continue
            seen.add(identity)
            groups[fields['category']].append((fields, summary, identity))
    examples = []
    while len(examples) < count and any(groups.values()):
        for category in sorted(groups):
            if not groups[category] or len(examples) >= count:
                continue
            fields, summary, identity = groups[category].popleft()
            cleaned = redact(summary)
            examples.append({'id': 'article-' + identity[:16],
                **{k: redact(v) for k, v in fields.items()},
                'output': cleaned, 'output_sha256_before_redaction': sha(summary.encode()),
                'redacted': cleaned != summary, 'output_kind': 'persisted_postprocessed_summary_not_proven_raw_response',
                'raw_llm_output': None, 'source_excerpt': None, 'fidelity': 'not_evaluable_without_source_reference',
                'generation_metrics': None, 'historical_model': None, 'historical_prompt': None})
    if len(examples) < 15:
        raise ExportError('insufficient_valid_examples')
    report = {'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'kind': 'private_live_readonly_sample', 'snapshot': 'per_file_consistent_not_cross_file_transaction',
        'inputs': inputs, 'skipped_records': skipped, 'selection': 'category_round_robin_latest_file_first_unique_identity',
        'observed_model': observed_model(state), 'prompt_reference': prompt_reference(repo),
        'examples': examples, 'radio': radio_inventory(state), 'publication_authorized': False}
    review = {'version': 1, 'private_sha256': sha(json_text(report).encode()),
        'approvals': {e['id']: {'privacy_reviewed': False, 'output_rights_reviewed': False,
                               'review_note': ''} for e in examples}}
    draft = {'version': 1, 'examples': [{'id': e['id'], 'output': '[WITHHELD: privacy and rights review required]'} for e in examples],
        'radio': {'scripts_recovered': report['radio']['scripts_recovered']}, 'publication_authorized': False}
    output_bundle(destination, {'private.json': json_text(report), 'private.md': markdown(report),
        'review.json': json_text(review), 'share-draft.json': json_text(draft), 'share-draft.md': markdown(draft)}, (state, repo))
    return {'status': 'prepared_private', 'examples': len(examples), 'categories': len({e['category'] for e in examples}),
        'scripts_recovered': report['radio']['scripts_recovered'], 'uploaded': False}


def share(private, review, destination):
    raw = read(private)
    report = decode(raw)
    approvals = decode(read(review))
    if not isinstance(report, dict) or not isinstance(approvals, dict):
        raise ExportError('invalid_report_or_review')
    if not isinstance(report.get('examples'), list) or not isinstance(approvals.get('approvals'), dict):
        raise ExportError('invalid_report_or_review')
    if approvals.get('private_sha256') != sha(raw) or approvals.get('version') != 1:
        raise ExportError('review_version_conflict')
    result = []
    for row in report['examples']:
        approval = approvals.get('approvals', {}).get(row['id'], {})
        if approval.get('privacy_reviewed') is not True or approval.get('output_rights_reviewed') is not True:
            continue
        if not isinstance(approval.get('review_note'), str) or not approval['review_note'].strip():
            raise ExportError('review_note_required')
        projected = {k: redact(text(row[k]), public=True) for k in ('id', 'title', 'title_original', 'category', 'source', 'output', 'pub_date', 'timestamp')}
        projected['link'] = safe_url(row['link'], public=True)
        projected['fidelity'] = 'not_evaluated'
        result.append(projected)
    if not result:
        raise ExportError('no_reviewed_examples')
    value = {'version': 1, 'examples': result, 'radio': {'scripts_recovered': 0},
        'publication_authorized': False, 'operator_publication_approval_still_required': True}
    output_bundle(destination, {'share.json': json_text(value), 'share.md': markdown(value)}, (Path(private).parent,))
    return {'status': 'prepared_reviewed_local_only', 'examples': len(result), 'uploaded': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('export')
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--count', type=int, default=20)
    p = sub.add_parser('share')
    p.add_argument('--private', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export(args.state, args.repo, args.output, args.count) if args.command == 'export' else share(args.private, args.review, args.output)
        print(json.dumps(result))  # Never content, paths or provider errors.
    except (ValueError, OSError, KeyError, TypeError, RecursionError, SyntaxError, AttributeError):
        print(json.dumps({'status': 'refused', 'reason': 'invalid_or_unsafe_input_output', 'uploaded': False}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
