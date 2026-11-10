import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock,patch
import pytest
from modules.core.llm_client import OllamaClient
from modules.core.llm_cache import LLMCache
from modules.fetch import atlas_fetch
from tests.test_editorial_integrity import make_fetcher,qualify
from tests.test_article_integrity import engine
from modules.http.external import HTTPStatusError


def client_at(path):
    with patch.object(OllamaClient,'_detect_model_from_server',return_value=None):
        return OllamaClient({'paths':{'data_dir':str(path)},'llm':{'provider':'llama-server','model':'fixture','cache':{'enabled':False}}})


def response(tokens=12):
    r=Mock();r.status=200;r.read.return_value=json.dumps({'choices':[{'message':{'content':'{"status":"ok","summary":"Le conseil vote."}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':tokens,'completion_tokens':15}}).encode();r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False)
    return r


def test_request_id_links_log_http_and_receipt_without_content(tmp_path,caplog):
    client=client_at(tmp_path);metrics={};identity='a'*32;caplog.set_level('INFO')
    with patch('urllib.request.urlopen',return_value=response()) as send:
        assert client.call('private input',request_id=identity,metrics=metrics)
    assert send.call_args.args[0].get_header('X-request-id')==identity
    assert metrics['request_id']==identity and metrics['transport_attempted'] is True
    assert metrics['http_status']==200 and metrics['finish_reason']=='stop' and metrics['prompt_tokens']==12
    assert identity in caplog.text and 'private input' not in caplog.text and 'Le conseil' not in caplog.text


def test_cache_hit_does_not_inherit_last_response_tokens(tmp_path):
    client=client_at(tmp_path);metrics={}
    with patch('urllib.request.urlopen',return_value=response(100)):
        client.call('q',metrics=metrics)
    client._cache=LLMCache(tmp_path/'cache');client._cache.put('saved','cached',caller='fetch',model='fixture')
    with patch('urllib.request.urlopen') as send:
        assert client.call('q',caller='fetch',cache_key='saved',metrics=metrics)=='cached'
    send.assert_not_called()
    assert metrics['cache_hit'] is True and metrics['transport_attempted'] is False
    assert metrics['prompt_tokens'] is None and metrics['response_duration_ms'] is None


def test_concurrent_calls_keep_thread_local_receipts(tmp_path):
    client=client_at(tmp_path);client._acquire_lock=Mock(return_value=True);client._release_lock=Mock()
    barrier=threading.Barrier(2)
    def send(request,**kwargs):
        value=int(json.loads(request.data)['messages'][0]['content']);barrier.wait(timeout=3);return response(value)
    def invoke(value):
        receipt={};identity=str(value)*32;client.call(str(value),request_id=identity,metrics=receipt);return receipt
    with patch('urllib.request.urlopen',side_effect=send),ThreadPoolExecutor(2) as pool:
        results=list(pool.map(invoke,(1,2)))
    assert [(r['request_id'],r['prompt_tokens']) for r in results]==[('1'*32,1),('2'*32,2)]


def test_trace_receives_only_its_exact_receipt(tmp_path):
    obj=qualify(make_fetcher(tmp_path),prompt_version='editorial-v2.1');client=client_at(tmp_path/'client')
    with patch.object(obj,'_generation_client',return_value=client),patch('modules.fetch.atlas_fetch.get_language',return_value='fr'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=20),patch('urllib.request.urlopen',return_value=response(72)):
        assert obj._summarize('Un vote','Le conseil vote.','france')=='Le conseil vote.'
    evidence=obj._summary_evidence
    assert evidence['generation_receipt']['request_id']==evidence['request_id']
    assert evidence['tokens']['prompt']==72 and evidence['finish_reason']=='stop'


def test_http_failure_before_generation_has_no_id_or_metrics(engine,monkeypatch):
    cfg=dict(engine.config);cfg['fetch']={'editorial':{'prompt_version':'editorial-v2.1','trace':{'enabled':True}}};engine._apply_config(cfg)
    monkeypatch.setattr(engine,'_fetch_content',Mock(side_effect=HTTPStatusError(403)))
    send=Mock();monkeypatch.setattr(atlas_fetch,'ollama_call',send)
    assert engine.run()==0
    send.assert_not_called()
    traces=list((engine.seen_file.parent/'editorial_traces').glob('*.json'));assert traces
    for path in traces:
        record=json.loads(path.read_text())
        assert record['summary_generation']=={} and record['title_generation']=={}


@pytest.mark.parametrize('bad',['not-a-uuid','x\n'*16,123])
def test_invalid_correlation_is_rejected_before_any_provider(tmp_path,bad):
    client=client_at(tmp_path)
    with patch('urllib.request.urlopen') as send:
        with pytest.raises(ValueError,match='Invalid generation request ID'):client.call('q',request_id=bad)
        send.assert_not_called()


def test_payload_failure_before_transport_is_not_attributed_to_provider(tmp_path):
    client=client_at(tmp_path);metrics={}
    with patch('urllib.request.urlopen') as send:
        assert client.call(object(),metrics=metrics)==''
    send.assert_not_called()
    assert metrics['transport_attempted'] is False and metrics['http_status'] is None
    assert metrics['request_parameters_sent'] is None


def test_cli_trace_does_not_claim_http_parameters_were_sent(tmp_path):
    cfg={'paths':{'data_dir':str(tmp_path)},'llm':{'provider':'ollama','model':'fixture'},
         'fetch':{'editorial':{'prompt_version':'editorial-v2.1'}}}
    client=OllamaClient(cfg);client._call_ollama_cli=Mock(return_value='{"status":"ok","summary":"Le conseil vote."}')
    obj=make_fetcher(tmp_path);obj._apply_config(cfg)
    with patch.object(obj,'_generation_client',return_value=client),patch('modules.fetch.atlas_fetch.get_language',return_value='fr'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=20):
        obj._summarize('Un vote','Le conseil vote.','france')
    assert obj._summary_evidence['request_parameters']=={'max_tokens':None,'temperature':None}
    assert obj._summary_evidence['generation_receipt']['kind']=='cli_uninstrumented'
    assert obj._summary_evidence['generation_receipt']['request_parameters_sent'] is None
