"""Offline benchmark planner/analyser. Never loads models or contacts a provider."""
import argparse
import json
import re
from pathlib import Path
from deploy.editorial_qualification import ExportError, decode, read, sha, text, json_text, output_bundle, redact, safe_url

RUBRIC = ('source_fidelity', 'important_facts_retained', 'french_quality',
          'repetitions_and_artificial_style', 'instructions_and_voice_tags')
PARAMETERS = {'temperature': 0.3, 'seed': 42, 'max_tokens': 4096, 'timeout_seconds': 300}


def plan(corpus_path, models_path, output, repetitions=3):
    if type(repetitions) is not int or not 1 <= repetitions <= 5:
        raise ExportError('repetition_limit')
    corpus_raw, models_raw = read(corpus_path), read(models_path)
    corpus, models = decode(corpus_raw), decode(models_raw)
    if not isinstance(corpus, dict) or not isinstance(models, dict):
        raise ExportError('invalid_schema')
    if corpus.get('version') != 1 or models.get('version') != 1:
        raise ExportError('invalid_schema')
    cases, candidates = corpus.get('cases'), models.get('models')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 25 or not isinstance(candidates, list) or not 1 <= len(candidates) <= 5:
        raise ExportError('bounded_cases_models_required')
    ids = set()
    for case in cases:
        if set(case) != {'id', 'task', 'prompt', 'prompt_version', 'reference_excerpt', 'reference_status'}:
            raise ExportError('invalid_case_schema')
        if not re.fullmatch(r'[a-z0-9-]{1,64}', case['id']) or case['id'] in ids or case['task'] not in ('summary', 'radio'):
            raise ExportError('invalid_case_identity')
        ids.add(case['id'])
        for key in ('prompt', 'prompt_version', 'reference_excerpt', 'reference_status'):
            text(case[key], 64000 if key == 'prompt' else 2000)
            if redact(case[key]) != case[key]:
                raise ExportError('sensitive_case_refused')
        if not case['prompt'].strip() or not case['prompt_version'].strip():
            raise ExportError('prompt_required')
        if redact(case['prompt']) != case['prompt']:
            raise ExportError('sensitive_prompt_refused')
    ids = set()
    for candidate in candidates:
        if set(candidate) != {'id', 'upstream', 'revision', 'quantization', 'weights_sha256', 'runtime_version', 'hardware', 'compatibility', 'reasoning_mode'}:
            raise ExportError('invalid_model_schema')
        if not re.fullmatch(r'[a-z0-9-]{1,64}', candidate['id']) or candidate['id'] in ids:
            raise ExportError('invalid_model_identity')
        ids.add(candidate['id'])
        for key, value in candidate.items():
            if value is not None:
                text(value, 500)
                if redact(value) != value:
                    raise ExportError('sensitive_model_metadata_refused')
        if not isinstance(candidate['upstream'], str) or safe_url(candidate['upstream'], public=True) != candidate['upstream']:
            raise ExportError('safe_upstream_required')
        if candidate['reasoning_mode'] not in (None, 'off', 'on'):
            raise ExportError('explicit_reasoning_mode_required')
        if candidate['weights_sha256'] is not None and not re.fullmatch(r'[0-9a-f]{64}', candidate['weights_sha256']):
            raise ExportError('invalid_model_digest')
    value = {'version': 1, 'kind': 'offline_plan_not_executed', 'generation_executed': False,
        'corpus_sha256': sha(corpus_raw), 'models_sha256': sha(models_raw),
        'cases': cases, 'models': candidates, 'repetitions': repetitions,
        'parameters': PARAMETERS, 'parameters_sha256': sha(json_text(PARAMETERS).encode()),
        'thinking_mode': 'per_model_explicit_operator_declaration_required_for_real_runs',
        'quality_scores': None, 'publication_authorized': False}
    reviews = {'version': 1, 'plan_sha256': sha(json_text(value).encode()), 'scale': '0..4 or null; no score without evidence',
        'reviews': [{'case_id': c['id'], 'blind_model_label': None, 'reviewer': None,
                     'reference_status': c['reference_status'],
                     'criteria': {k: {'score': None, 'source_evidence': None, 'output_example': None, 'comment': None} for k in RUBRIC}}
                    for c in cases]}
    output_bundle(output, {'plan.json': json_text(value), 'human-review.json': json_text(reviews)},
                  (Path(corpus_path), Path(models_path)))
    return {'status': 'planned_not_executed', 'cases': len(cases), 'models': len(candidates), 'network_calls': 0}


def compare(plan_path, runs_path, output):
    raw = read(plan_path)
    protocol, runs = decode(raw), decode(read(runs_path))
    if not isinstance(protocol, dict) or not isinstance(runs, dict):
        raise ExportError('invalid_schema')
    if protocol.get('version') != 1 or runs.get('version') != 1 or runs.get('plan_sha256') != sha(raw):
        raise ExportError('plan_version_conflict')
    if runs.get('kind') not in ('fixture_replay', 'operator_measurement'):
        raise ExportError('measurement_provenance_required')
    rows = runs.get('runs')
    if not isinstance(rows, list) or len(rows) > 625:
        raise ExportError('run_limit')
    cases = {c['id']: c for c in protocol['cases']}
    models = {m['id']: m for m in protocol['models']}
    if runs['kind'] == 'operator_measurement':
        for model in models.values():
            if any(not model.get(k) for k in ('revision', 'quantization', 'weights_sha256', 'runtime_version', 'hardware', 'reasoning_mode')) or model.get('compatibility') != 'operator_validated':
                raise ExportError('exact_model_environment_required')
        if len({m['reasoning_mode'] for m in models.values()}) != 1:
            raise ExportError('reasoning_policy_must_be_controlled')
    seen = set()
    results = []
    for row in rows:
        required = {'case_id', 'model_id', 'repetition', 'prompt_sha256', 'parameters_sha256',
                    'output', 'duration_ms', 'prompt_tokens', 'completion_tokens', 'finish_reason', 'error'}
        if not isinstance(row, dict) or set(row) != required:
            raise ExportError('invalid_run_schema_no_reasoning_fields_allowed')
        case, model = row['case_id'], row['model_id']
        repetition = row['repetition']
        identity = (case, model, repetition)
        if case not in cases or model not in models or type(repetition) is not int or not 1 <= repetition <= protocol['repetitions'] or identity in seen:
            raise ExportError('unknown_or_duplicate_run')
        seen.add(identity)
        if row['prompt_sha256'] != sha(cases[case]['prompt'].encode()) or row['parameters_sha256'] != protocol['parameters_sha256']:
            raise ExportError('unfair_comparison_inputs_changed')
        output_text = text(row['output'], 64000)
        if redact(output_text) != output_text:
            raise ExportError('sensitive_or_reasoning_output_refused')
        for key in ('duration_ms', 'prompt_tokens', 'completion_tokens'):
            v = row[key]
            if v is not None and (type(v) is not int or not 0 <= v <= 10**9):
                raise ExportError('invalid_measurement')
        if row['finish_reason'] not in ('stop', 'length', 'unknown') or row['error'] not in (None, 'timeout', 'http_error', 'empty', 'invalid_response', 'cancelled'):
            raise ExportError('typed_failure_required')
        sentences = [s.strip() for s in re.split(r'[.!?]+', output_text) if s.strip()]
        results.append({**row, 'word_count': len(output_text.split()),
            'voice_tag_count': len(re.findall(r'\[VOIX\w*\]', output_text)),
            'exact_repeated_sentences': len(sentences)-len(set(sentences)),
            'truncated': row['finish_reason'] == 'length',
            'failed': bool(row['error']) or not output_text.strip(),
            'factual_accuracy_score': None, 'human_review_required': True})
    summaries = []
    for model in models:
        selected = [r for r in results if r['model_id'] == model]
        durations = [r['duration_ms'] for r in selected if r['duration_ms'] is not None]
        summaries.append({'model_id': model, 'observed_runs': len(selected),
            'missing_runs': len(cases)*protocol['repetitions']-len(selected),
            'failed_runs': sum(r['failed'] for r in selected),
            'truncated_runs': sum(r['truncated'] for r in selected),
            'failure_rate_observed': sum(r['failed'] for r in selected)/len(selected) if selected else None,
            'mean_duration_ms': sum(durations)/len(durations) if durations else None,
            'timing_evidence': 'simulated_not_real' if runs['kind'] == 'fixture_replay' else 'operator_declared_not_independently_verified',
            'factual_accuracy_score': None})
    report = {'version': 1, 'kind': runs['kind'], 'plan_sha256': sha(raw), 'results': results,
        'models': summaries, 'automatic_truth_scores': False, 'model_recommendation': None,
        'publication_authorized': False}
    lines = ['# Comparaison éditoriale hors ligne', '', 'Provenance : '+runs['kind'], '',
        'Aucun jugement automatique de vérité. Les temps fixture_replay sont simulés.', '',
        '| Modèle | Observés | Manquants | Échecs | Tronqués | Durée moyenne ms |',
        '|---|---:|---:|---:|---:|---:|']
    for s in summaries:
        lines.append('| '+s['model_id']+' | '+ ' | '.join(str(s[k]) if s[k] is not None else 'non mesuré'
            for k in ('observed_runs', 'missing_runs', 'failed_runs', 'truncated_runs', 'mean_duration_ms'))+' |')
    output_bundle(output, {'comparison.json': json_text(report), 'comparison.md': '\n'.join(lines)+'\n'},
                  (Path(plan_path), Path(runs_path)))
    return {'status': 'analysed_offline', 'runs': len(results), 'truth_scores': False, 'network_calls': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    x = sub.add_parser('plan')
    x.add_argument('--corpus', required=True, type=Path);x.add_argument('--models', required=True, type=Path)
    x.add_argument('--repetitions', type=int, default=3);x.add_argument('--output', required=True, type=Path)
    x = sub.add_parser('compare')
    x.add_argument('--plan', required=True, type=Path);x.add_argument('--runs', required=True, type=Path)
    x.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    try:
        result = plan(args.corpus,args.models,args.output,args.repetitions) if args.command=='plan' else compare(args.plan,args.runs,args.output)
        print(json.dumps(result))
    except (ValueError, OSError, TypeError, KeyError, RecursionError, AttributeError):
        print(json.dumps({'status':'refused','reason':'invalid_or_incomparable_input','network_calls':0}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
