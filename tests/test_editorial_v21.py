"""Contract and regression tests only. No claim of measured Ornith quality."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock,patch
import pytest
from modules.fetch import editorial as ed
from modules.fetch.atlas_fetch import ArticleFetcher
from modules.fetch.extraction import extract_article_text
from modules.http import external as http
from deploy import editorial_prompt_trial as trial
from tests.test_editorial_integrity import make_fetcher,qualify


def test_prompt_variants_keep_identical_source_window_and_distinct_identity(tmp_path):
    result=trial.prepare(tmp_path/'plan')
    assert result=={'status':'prepared_not_measured','cases':13,'calls':39,'network_calls':0}
    plan=json.loads((tmp_path/'plan/plan.json').read_text())
    assert plan['model_results'] is None and plan['truth_scores'] is None
    for case in json.loads(trial.CORPUS.read_text())['cases']:
        rows=[r for r in plan['jobs'] if r['case_id']==case['id']]
        assert {r['version'] for r in rows}==set(trial.VERSIONS)
        assert {r['transmitted_source_sha256'] for r in rows}=={hashlib.sha256(case['source'][:3000].encode()).hexdigest()}
        assert len({r['prompt_sha256'] for r in rows})==3


def test_v21_instruction_does_not_label_source_facts_unreliable():
    policy=ed.options({'fetch':{'editorial':{'prompt_version':'editorial-v2.1'}}})
    prompt=ed.summary_prompt('Une ouverture','La bibliothèque ouvre samedi.','monde','fr',policy)
    assert 'source non fiable' not in prompt
    assert 'une ou deux phrases' in prompt and 'aucun fait exploitable' in prompt
    assert json.dumps({'title':'Une ouverture','category':'monde','source':'La bibliothèque ouvre samedi.'},ensure_ascii=False) in prompt
    assert ed.parse_summary('{"status":"ok","summary":"La bibliothèque ouvre samedi."}','editorial-v2.1')=='La bibliothèque ouvre samedi.'
    with pytest.raises(ValueError,match='summary_source_insufficient'):
        ed.parse_summary('{"status":"insufficient_source","summary":""}','editorial-v2.1')


def test_legacy_extraction_can_choose_a_long_consent_container_but_v21_uses_body():
    html='<main><div role="dialog">'+('Choisir les cookies. '*200)+'</div><article><div itemprop="articleBody"><p>Valmer vote 240 000 € pour un pont.</p></div></article></main>'
    response=Mock(text=html,content=html.encode(),url='https://example.com/news')
    with patch('modules.fetch.atlas_fetch.source_http.get',return_value=response):
        old=ArticleFetcher._fetch_content('https://example.com/news',raise_errors=True)
        new=ArticleFetcher._fetch_content('https://example.com/news',raise_errors=True,editorial_version='editorial-v2.1')
    assert old.startswith('Choisir les cookies.') and len(old)>3000
    assert new=='Valmer vote 240 000 € pour un pont.'


@pytest.mark.parametrize('text',['Aono 青野 ouvre samedi.','La bibliothèque ouvre.','La salle expose des affiches sur les cookies.'])
def test_short_unicode_body_and_legitimate_cookie_words_preserved(text):
    assert extract_article_text('<article><p>'+text+'</p></article>')==text


@pytest.mark.parametrize('status',[403,429])
def test_extraction_never_bypasses_or_retries_http_refusal(status):
    response=Mock();response.raise_for_status.side_effect=http.HTTPStatusError(status)
    with patch('modules.fetch.atlas_fetch.source_http.get',return_value=response) as send:
        with pytest.raises(http.HTTPStatusError) as exc:
            ArticleFetcher._fetch_content('https://example.com/news',raise_errors=True,editorial_version='editorial-v2.1')
    assert exc.value.status_code==status and send.call_count==1


def test_actual_prompt_version_separates_cache_without_deleting_legacy(tmp_path):
    obj=qualify(make_fetcher(tmp_path),prompt_version='editorial-v2.1')
    with patch('modules.fetch.atlas_fetch.get_language',return_value='fr'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=30),patch('modules.fetch.atlas_fetch.ollama_call',return_value='{"status":"ok","summary":"Le conseil vote."}') as call:
        assert obj._summarize('Un vote','Le conseil vote.','france')=='Le conseil vote.'
    assert 'summary-editorial-v2.1' in call.call_args.args[0]
    assert obj._summary_evidence['transmitted_characters']==len('Le conseil vote.')
    assert obj._summary_evidence['source_window_limit']==3000
    assert len(obj._summary_evidence['request_id'])==32
    # A mock that supplied no receipt must not be assigned invented metrics.
    assert 'generation_receipt' not in obj._summary_evidence
    assert obj._summary_evidence['tokens'] is None


def test_configured_category_is_not_inferred_from_article_topic(tmp_path):
    obj=make_fetcher(tmp_path);obj._state={'version':1,'records':{}}
    item={'title':'Un musée fictif expose des jeux','link':'https://example.com/culture','pub_date':'',
          'ingestion':{'source_key':hashlib.sha256(b'https://example.com/all-news').hexdigest(),'category':'culture'}}
    record=obj._record(item,'geopolitique')
    assert record['category']=='geopolitique'
    assert record['ingestion']['category']=='culture'
    assert obj._record(item,'monde')['category']=='geopolitique' # Existing historical identity is preserved.


def test_measure_cannot_contact_shared_server_or_run_without_authorization(tmp_path):
    trial.prepare(tmp_path/'plan')
    with patch.object(trial,'IsolatedClient') as client:
        with pytest.raises(ValueError,match='explicit_execute_required'):
            trial.measure(tmp_path/'plan/plan.json',tmp_path/'absent',tmp_path/'result')
        example=trial.CORPUS.parent/'isolated-ornith-authorization.example.json'
        with pytest.raises(ValueError,match='isolated_operator_authorization_required'):
            trial.measure(tmp_path/'plan/plan.json',example,tmp_path/'result',execute=True)
        client.assert_not_called()


def test_authorization_refuses_shared_port_even_if_other_fields_are_present(tmp_path):
    raw=json.loads((trial.CORPUS.parent/'isolated-ornith-authorization.example.json').read_text())
    raw.update(approved=True,isolated_from_atlas_lab=True,endpoint='http://127.0.0.1:8080')
    p=tmp_path/'approval.json';p.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='isolated_operator_authorization_required'):trial.authorization(p)


def test_redirect_transport_never_follows_another_destination():
    import urllib.request
    request=urllib.request.Request(trial.ENDPOINT+'/v1/models')
    with pytest.raises(urllib.error.HTTPError):
        trial.NoRedirect().redirect_request(request,None,302,'redirect',{},'http://127.0.0.1:8080/v1/models')


@pytest.mark.parametrize('version,digest',[
    ('legacy-v1','a1453e618d167849babfc3ab27a230d3f6c60ce45564a30dc7fa186944aa81eb'),
    ('editorial-v2','98c902ced2db5f9533906e7a418a65db55e5d7745cc4948e2543f02696e96ffd')])
def test_previous_prompt_bytes_are_preserved(version,digest):
    prompt=ed.summary_prompt('Un pont','Une source courte.','france','fr',ed.options({'fetch':{'editorial':{'prompt_version':version}}}))
    assert hashlib.sha256(prompt.encode()).hexdigest()==digest


def valid_authorization(tmp_path):
    raw=json.loads((trial.CORPUS.parent/'isolated-ornith-authorization.example.json').read_text())
    raw.update(approved=True,isolated_from_atlas_lab=True,model='fixture-Ornith',weights_sha256='1'*64,
               runtime_version='synthetic-fixture-only',hardware='fixture-not-real-hardware',server_parameters={'context_size':8192,'threads':1,'gpu_layers':0})
    path=tmp_path/'approval.json';path.write_text(json.dumps(raw));return path


def test_unbalanced_protocol_fails_before_any_provider(tmp_path):
    trial.prepare(tmp_path/'plan');path=tmp_path/'plan/plan.json';plan=json.loads(path.read_text());plan['jobs'].pop();path.write_text(json.dumps(plan))
    approval=valid_authorization(tmp_path)
    with patch.object(trial,'IsolatedClient') as provider:
        with pytest.raises(ValueError,match='balanced_complete_protocol_required'):trial.measure(path,approval,tmp_path/'result',execute=True)
        provider.assert_not_called()


def test_output_in_git_is_refused_before_any_provider(tmp_path):
    trial.prepare(tmp_path/'plan');approval=valid_authorization(tmp_path);folder=tmp_path/'repo';folder.mkdir();(folder/'.git').mkdir();(folder/'.git/HEAD').write_text('ref: refs/heads/fixture')
    with patch.object(trial,'IsolatedClient') as provider:
        with pytest.raises(ValueError,match='private_output_outside_git_required'):
            trial.measure(tmp_path/'plan/plan.json',approval,folder/'result',execute=True)
        provider.assert_not_called()


def test_runner_http_double_checks_protocol_not_ornith_quality(tmp_path,monkeypatch):
    # This test produces only temporary synthetic records, never published measurements.
    from tests.test_llm_correlation import response
    trial.prepare(tmp_path/'plan');approval=valid_authorization(tmp_path)
    def detect(self):self.detected=self.model;return self.model
    monkeypatch.setattr(trial.IsolatedClient,'_detect_model_from_server',detect)
    monkeypatch.setattr(trial.IsolatedClient,'_open_http',lambda *a,**k:response(40))
    result=trial.measure(tmp_path/'plan/plan.json',approval,tmp_path/'measurement',execute=True)
    assert result['runs']==39 and result['quality_validated'] is False
    report=json.loads((tmp_path/'measurement/measurements.json').read_text())
    assert report['truth_scores'] is None and report['deployment_authorized'] is False
    assert all(r['human_review']['invented_information'] is None for r in report['runs'])
    assert len({r['request_id'] for r in report['runs']})==39
    assert all(r['receipt']['request_id']==r['request_id'] for r in report['runs'])
    assert all(r['receipt']['cache_hit'] is False for r in report['runs'])
    assert report['summaries'][0]['invalid_json_syntax'] is None # Legacy has no JSON contract.
