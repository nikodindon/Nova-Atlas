"""Offline protocol/evidence tests; no assertion of measured Ornith quality."""
import hashlib
import json
from pathlib import Path
import pytest
from modules.fetch import editorial as ed
from deploy import editorial_prompt_trial as trial
from tests.test_article_integrity import engine, record, ITEM, feed_result
from tests.test_editorial_pipeline import enable

TITLE='Une élection municipale à Valmer'
BODY='Le club fictif des Ours de Belrive a remporté une rencontre de hockey 3 à 2 dimanche. Son entraîneur annonce un déplacement mardi.'


def test_old_prompt_bytes_are_frozen_for_every_original_case_and_profile():
    cases={c['id']:c for c in json.loads(trial.CORPUS.read_text())['cases']}
    for row in json.loads((Path(__file__).parent/'fixtures/editorial-old-prompt-hashes.json').read_text()):
        c=cases[row['case_id']]
        policy=ed.options({'fetch':{'editorial':{'prompt_version':row['version'],'length_profile':row['profile']}}})
        prompt=ed.summary_prompt(c['title'],c['source'],c.get('category','qualification'),'fr',policy)
        assert hashlib.sha256(prompt.encode()).hexdigest()==row['sha256']


def test_concrete_mismatch_has_event_evidence_not_lexical_verdict():
    r=ed.review(TITLE,BODY,BODY)
    assert 'title_source_event_mismatch' in r['blocking_reasons']
    assert r['evidence'][0]['basis']==['explicit_election_title','named_sport','result_action','numeric_score','short_body_without_election_context']
    assert r['factually_verified'] is False


@pytest.mark.parametrize('title,body',[
    ('Municipal election in Valmer','The hockey club won 3-2 on Sunday.'),
    (TITLE,BODY),
])
def test_standalone_explicit_events(title,body):
    assert ed.title_source_evidence(title,body)


@pytest.mark.parametrize('title,body',[
    (TITLE,BODY+' Une élection municipale aura lieu dimanche.'),
    (TITLE,'Le maire évoque le hockey et une victoire 3 à 2 lors du scrutin.'),
    ('Valmer',BODY),
    ('青野市の選挙',BODY),
    ('Le club de Лесной remporte son match',BODY),
    ('Municipal election in Valmer','Les électeurs de Valmer voteront dimanche.'),
    (TITLE,'Le club de hockey a remporté son match.'), # no score
    (TITLE,'Le club a remporté son match 3 à 2.'), # no named sport
    (TITLE,'Le hockey n’a pas remporté la victoire 3 à 2.'),
    (TITLE,'Selon le club de hockey, il a remporté son match 3 à 2.'),
    (TITLE,'« Le club de hockey a remporté son match 3 à 2 », annonce le journal.'),
    (TITLE,BODY+' Développement. '*70), # long article: insufficient evidence of exclusivity
    ('Une élection municipale et du hockey à Valmer',BODY),
    (TITLE,'Le conseil a voté un budget de 120 euros.'), # lexical absence is not contradiction
])
def test_uncertain_translated_short_multitopic_or_negated_not_blocked(title,body):
    assert not ed.title_source_evidence(title,body)
    assert 'title_source_event_mismatch' not in ed.review(title,body)['blocking_reasons']


@pytest.mark.parametrize('checks,published',[('off',1),('observe',1),('strict',0)])
def test_pipeline_observe_vs_strict_preserves_retry_semantics(engine,checks,published):
    enable(engine,prompt_version='editorial-v2.2',checks=checks)
    engine._fetch_rss.return_value=feed_result([{**ITEM,'title':TITLE}])
    engine._fetch_content.return_value=BODY
    engine._summarize.return_value=BODY
    assert engine.run()==published
    r=record(engine)
    if checks=='strict':
        assert r['status']=='retryable' and r['reason']=='editorial_needs_review'
        assert not engine._load_seen() and not engine._load_today()
    elif checks=='observe':
        assert r['editorial_review']['evidence'] and r['editorial_review']['status']=='needs_review'


def test_strict_cache_rejects_mismatch_but_observe_keeps_it():
    raw=json.dumps({'status':'ok','summary':BODY})
    assert not ed.summary_cacheable(raw,'editorial-v2.2',title=TITLE,content=BODY,checks='strict')
    assert ed.summary_cacheable(raw,'editorial-v2.2',title=TITLE,content=BODY,checks='observe')


def test_v22_contract_and_cache_identity_do_not_rewrite_grammar():
    prompts=[]
    for v in ed.VERSIONS:
        p=ed.summary_prompt('Musée de Лесной','Les prix restent inchangés.','culture','fr',ed.options({'fetch':{'editorial':{'prompt_version':v}}}))
        prompts.append(ed.cache_key(p,v,'fr','culture','same-model'))
    assert len(set(prompts))==4
    raw='{"status":"ok","summary":"Les prix d’entrée rester inchangés. Лесной, 180 kilomètres."}'
    assert ed.parse_summary(raw,'editorial-v2.2')=='Les prix d’entrée rester inchangés. Лесной, 180 kilomètres.'
    # Faithful cleaning does not pretend to repair grammar with regex.


def test_small_comparison_offline_and_balanced_with_original_matrix_unchanged(tmp_path):
    assert trial.prepare(tmp_path/'focused',suite='v22-focused')=={'status':'prepared_not_measured','cases':8,'calls':16,'network_calls':0}
    raw,plan,corpus,jobs=trial.validated_protocol(tmp_path/'focused/plan.json')
    assert len(jobs)==16 and {j['version'] for j in jobs}=={'editorial-v2.1','editorial-v2.2'}
    assert plan['parameters']==trial.PARAMETERS and plan['model_results'] is None
    for c in corpus['cases']:
        rows=[j for j in jobs if j['case_id']==c['id']]
        assert len({j['transmitted_source_sha256'] for j in rows})==1
    auth=tmp_path/'authorization.json';auth.write_text('{}')
    trial.export_measurements(raw,plan,corpus,jobs,{},[],tmp_path/'empty-report',tmp_path/'focused/plan.json',auth)
    report=json.loads((tmp_path/'empty-report/measurements.json').read_text())
    assert {r['version'] for r in report['summaries']}=={'editorial-v2.1','editorial-v2.2'}
    assert all(r['observed_runs']==0 for r in report['summaries'])
    assert trial.prepare(tmp_path/'old')['calls']==39
    assert len(trial.validated_protocol(tmp_path/'old/plan.json')[3])==39
    plan['jobs']=plan['jobs'][:-1]
    (tmp_path/'focused/plan.json').write_text(json.dumps(plan))
    with pytest.raises(Exception,match='balanced_complete_protocol_required'):
        trial.validated_protocol(tmp_path/'focused/plan.json')
