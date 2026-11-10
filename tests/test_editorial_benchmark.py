import json
from pathlib import Path
import pytest
from deploy import editorial_benchmark as b
from deploy.editorial_qualification import ExportError,sha,json_text

@pytest.fixture
def corpus(tmp_path):
    source=tmp_path/'input';source.mkdir()
    cases={'version':1,'cases':[{'id':'sample','task':'summary','prompt':'Résume la fixture publique.','prompt_version':'test-v1',
        'reference_excerpt':'La commune fictive inaugure deux bus.','reference_status':'synthetic_author_owned'}]}
    models={'version':1,'models':[{'id':m,'upstream':'https://example.org/model','revision':None,'quantization':None,
        'weights_sha256':None,'runtime_version':None,'hardware':None,'compatibility':'not_tested','reasoning_mode':None} for m in ['alpha','beta']]}
    (source/'corpus.json').write_text(json.dumps(cases));(source/'models.json').write_text(json.dumps(models))
    output=tmp_path/'plan';b.plan(source/'corpus.json',source/'models.json',output,2)
    return output/'plan.json'


def rows(plan):
    p=json.loads(plan.read_text());c=p['cases'][0]
    return {'version':1,'plan_sha256':sha(plan.read_bytes()),'kind':'fixture_replay',
        'runs':[{'case_id':c['id'],'model_id':'alpha','repetition':1,'prompt_sha256':sha(c['prompt'].encode()),
            'parameters_sha256':p['parameters_sha256'],'output':'Deux bus arrivent. Deux bus arrivent.', 'duration_ms':120,
            'prompt_tokens':None,'completion_tokens':None,'finish_reason':'stop','error':None}]}


def run_compare(plan,value,tmp_path):
    inp=tmp_path/'runs.json';inp.write_text(json.dumps(value));b.compare(plan,inp,tmp_path/'comparison')
    return json.loads((tmp_path/'comparison/comparison.json').read_text())


def test_plan_is_offline_no_generated_or_truth_claim(corpus):
    p=json.loads(corpus.read_text());assert p['generation_executed'] is False
    assert p['quality_scores'] is None and p['publication_authorized'] is False
    rubric=json.loads((corpus.parent/'human-review.json').read_text())
    assert all(c['score'] is None for c in rubric['reviews'][0]['criteria'].values())


def test_comparison_missing_runs_are_never_success(corpus,tmp_path):
    report=run_compare(corpus,rows(corpus),tmp_path)
    assert report['models'][0]['missing_runs']==1 and report['models'][1]['missing_runs']==2
    assert report['models'][1]['mean_duration_ms'] is None
    assert report['models'][0]['timing_evidence']=='simulated_not_real'
    assert report['automatic_truth_scores'] is False and report['model_recommendation'] is None
    assert report['results'][0]['exact_repeated_sentences']==1

@pytest.mark.parametrize('field',['prompt_sha256','parameters_sha256'])
def test_different_inputs_refuse_comparison(corpus,tmp_path,field):
    data=rows(corpus);data['runs'][0][field]='different'
    with pytest.raises(ExportError,match='inputs_changed'):run_compare(corpus,data,tmp_path)


def test_duplicate_runs_refused(corpus,tmp_path):
    data=rows(corpus);data['runs']*=2
    with pytest.raises(ExportError,match='duplicate'):run_compare(corpus,data,tmp_path)


def test_unknown_model_refused(corpus,tmp_path):
    data=rows(corpus);data['runs'][0]['model_id']='unknown'
    with pytest.raises(ExportError):run_compare(corpus,data,tmp_path)


def test_failures_truncation_and_unmeasured_time(corpus,tmp_path):
    data=rows(corpus);data['runs'][0].update(output='',error='timeout',finish_reason='length',duration_ms=None)
    report=run_compare(corpus,data,tmp_path)
    assert report['models'][0]['failed_runs']==1 and report['models'][0]['truncated_runs']==1
    assert report['models'][0]['mean_duration_ms'] is None


def test_simulated_timings_cannot_be_relabelled_without_exact_environment(corpus,tmp_path):
    data=rows(corpus);data['kind']='operator_measurement'
    with pytest.raises(ExportError,match='environment_required'):run_compare(corpus,data,tmp_path)

@pytest.mark.parametrize('field,value',[('reasoning_content','private'),('output','<think>private</think>summary'),
    ('duration_ms',-1),('prompt_tokens',True),('error','secret provider message')])
def test_reasoning_and_untyped_metrics_refused(corpus,tmp_path,field,value):
    data=rows(corpus);data['runs'][0][field]=value
    with pytest.raises(ExportError):run_compare(corpus,data,tmp_path)


def test_complete_operator_identity_remains_a_declared_measurement(corpus,tmp_path):
    protocol=json.loads(corpus.read_text())
    for model in protocol['models']:
        model.update(revision='test-revision',quantization='fixture',weights_sha256='0'*64,
                     runtime_version='fixture-runtime',hardware='fixture-host',compatibility='operator_validated',reasoning_mode='off')
    corpus.write_text(json.dumps(protocol))
    data=rows(corpus);data['kind']='operator_measurement'
    report=run_compare(corpus,data,tmp_path)
    assert report['models'][0]['timing_evidence']=='operator_declared_not_independently_verified'
    assert report['automatic_truth_scores'] is False
