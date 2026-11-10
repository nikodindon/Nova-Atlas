"""Offline reproductions of the failures observed on atlas-lab, 2026-11-10."""
import json
from unittest.mock import MagicMock, patch
import pytest
from modules.core.llm_client import OllamaClient

@pytest.fixture
def client(tmp_path):
    with patch.object(OllamaClient, '_detect_model_from_server', return_value=None):
        return OllamaClient({'paths': {'data_dir': str(tmp_path)}, 'llm': {
            'provider': 'llama-server', 'cache': {'enabled': False},
            'max_tokens_by_task': {'fetch': 1024, 'translate': 512, 'posts': 1024, 'flash': 1536}}})

def response(data):
    r=MagicMock();r.read.return_value=json.dumps(data).encode();r.__enter__.return_value=r
    return r

def reply(content='', finish='length', reasoning='private reasoning'):
    return {'choices':[{'message':{'content':content,'reasoning_content':reasoning},
                        'finish_reason':finish}], 'usage':{'completion_tokens':4096}}

def test_empty_length_is_not_retried_or_called_cold_start(client, caplog):
    with patch('urllib.request.urlopen', return_value=response(reply())) as send, patch('time.sleep') as sleep:
        assert client._call_llama_server('private prompt','m',20)==''
    assert send.call_count==1
    sleep.assert_not_called()
    assert 'output_limit' in caplog.text
    assert 'cold-start' not in caplog.text and 'private reasoning' not in caplog.text

def test_nonempty_truncated_answer_is_not_accepted(client):
    with patch('urllib.request.urlopen', return_value=response(reply('partial'))):
        assert client._call_llama_server('q','m',20)==''

def test_budget_by_caller(client):
    with patch('urllib.request.urlopen', return_value=response(reply('ok','stop'))) as send:
        assert client.call('q',caller='fetch')=='ok'
    assert json.loads(send.call_args.args[0].data)['max_tokens']==1024

@pytest.mark.parametrize('data',[None,[],{'choices':[]},{'choices':[None]},
    {'choices':[{'message':{'content':['bad']}}]}])
def test_malformed_response_is_classified(client,caplog,data):
    with patch('urllib.request.urlopen',return_value=response(data)):
        assert client._call_llama_server('q','m',20)==''
    assert 'invalid_response' in caplog.text

@pytest.mark.parametrize('code,kind',[(401,'authentication'),(402,'payment'),(403,'forbidden'),
    (429,'rate_limited'),(500,'provider_unavailable'),(404,'http_error')])
def test_http_errors_do_not_log_provider_body_or_retry(client,caplog,code,kind):
    import urllib.error
    error=urllib.error.HTTPError('http://private/token',code,'secret body',{},None)
    with patch('urllib.request.urlopen',side_effect=error) as send:
        assert client._call_llama_server('secret prompt','m',20)==''
    assert send.call_count==1 and kind in caplog.text
    assert 'secret' not in caplog.text and 'http://' not in caplog.text

@pytest.mark.parametrize('error,kind',[(TimeoutError('secret'),'timeout'),
    (ConnectionError('secret'),'transport_error'),(ValueError('secret'),'invalid_response')])
def test_failure_kind_and_no_identical_retry(client,caplog,error,kind):
    with patch('urllib.request.urlopen',side_effect=error) as send:
        assert client.call('q',caller='bulletin')==''
    assert send.call_count==1 and kind in caplog.text and 'secret' not in caplog.text
    assert client._acquire_lock('fetch',0)
    client._release_lock()

def test_success_logs_only_safe_metadata(client,caplog):
    caplog.set_level('INFO')
    data=reply('final answer','stop');data['usage'].update(
        prompt_tokens=100,completion_tokens_details={'reasoning_tokens':500})
    with patch('urllib.request.urlopen',return_value=response(data)):
        assert client.call('private prompt',caller='bulletin')=='final answer'
    assert 'reasoning_tokens=500' in caplog.text and 'reasoning_present=True' in caplog.text
    assert 'private' not in caplog.text and 'final answer' not in caplog.text

@pytest.mark.parametrize('task,expected',[('fetch',1024),('posts',1024),('flash',1536),
    ('bulletin',4096),('report',4096),('editions',4096)])
def test_each_task_has_its_budget(client,task,expected):
    with patch('urllib.request.urlopen',return_value=response(reply('ok','stop'))) as send:
        assert client.call('q',caller=task)=='ok'
    assert json.loads(send.call_args.args[0].data)['max_tokens']==expected


def test_translation_operation_selects_small_budget(client):
    with patch('urllib.request.urlopen',return_value=response(reply('ok','stop'))) as send:
        assert client.call('q',caller='fetch',operation='translate')=='ok'
    assert json.loads(send.call_args.args[0].data)['max_tokens']==512

@pytest.mark.parametrize('settings',[
 {'max_tokens_by_task':{'fetch':True}}, {'max_tokens_by_task':{'bad':200}},
 {'max_tokens_by_task':{'fetch':0}}, {'max_tokens_by_task':{'fetch':32769}},
 {'lock_wait_seconds':{'fetch':-1}}, {'lock_wait_seconds':{'fetch':True}},
 {'lock_wait_seconds':[]}, {'max_tokens_by_task':None}])
def test_invalid_budgets_fail_configuration_closed(tmp_path,settings):
    with pytest.raises(ValueError,match='Invalid LLM'):
        OllamaClient({'paths':{'data_dir':str(tmp_path)},'llm':settings})


def test_configured_wait_refuses_without_transport(client,caplog):
    client._wait_by_task={'fetch':0}
    with patch.object(client,'_acquire_lock',return_value=False) as lock, patch('urllib.request.urlopen') as send:
        assert client.call('q',caller='fetch')==''
    assert lock.call_args.kwargs['wait_max']==0
    send.assert_not_called()


def test_truncated_result_never_populates_cache(client,tmp_path):
    from modules.core.llm_cache import LLMCache
    client._cache=LLMCache(tmp_path/'cache')
    with patch('urllib.request.urlopen',return_value=response(reply('partial'))):
        assert client.call('q',caller='fetch',cache_key='key')==''
    assert client._cache.get('key') is None

@pytest.mark.parametrize('data,kind',[(reply(None,'stop'),'reasoning_only'),
    (reply('<think>private</think>answer','stop'),'reasoning_in_content'),
    (reply('answer','tool_calls'),'unsupported_finish')])
def test_reasoning_is_never_published(client,caplog,data,kind):
    with patch('urllib.request.urlopen',return_value=response(data)):
        assert client.call('q')==''
    assert kind in caplog.text and 'private' not in caplog.text


def test_large_response_is_bounded(client,caplog):
    r=response({});r.read.return_value=b'x'*(4*1024*1024+1)
    with patch('urllib.request.urlopen',return_value=r):
        assert client.call('q')==''
    r.read.assert_called_once_with(4*1024*1024+1)
    assert 'response_too_large' in caplog.text


def test_historical_budget_is_preserved_without_operator_tuning(tmp_path):
    with patch.object(OllamaClient, '_detect_model_from_server',return_value=None):
        client=OllamaClient({'paths':{'data_dir':str(tmp_path)},'llm':{'provider':'llama-server','cache':{'enabled':False}}})
    with patch('urllib.request.urlopen',return_value=response(reply('ok','stop'))) as send:
        assert client.call('q',caller='fetch')=='ok'
    assert json.loads(send.call_args.args[0].data)['max_tokens']==4096


def test_task_context_does_not_leak_to_next_request(client):
    with patch('urllib.request.urlopen',return_value=response(reply('ok','stop'))) as send:
        client.call('q',caller='fetch')
        assert json.loads(send.call_args.args[0].data)['max_tokens']==1024
        client._call_llama_server('q','m',10)
        assert json.loads(send.call_args.args[0].data)['max_tokens']==4096
