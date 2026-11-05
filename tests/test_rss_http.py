"""Conditional headers cannot widen the source HTTP security policy."""
from unittest.mock import Mock
import pytest
from modules.http.external import Conditional,FetchError,HTTPStatusError
from tests.test_http_security import client_for,FakeResponse,PUBLIC4


def test_304_requires_explicit_feed_mode_and_never_downloads_body():
    response=FakeResponse(status=304,headers={'ETag':'"a"','Content-Length':'99999999'})
    response.read1=Mock(side_effect=AssertionError('304 has no body'))
    client,calls,_=client_for([response])
    result=client.get('https://news.example/feed',conditional=Conditional('https://news.example/feed','"a"'),accept_not_modified=True)
    assert result.status_code==304 and result.content==b''
    assert calls[0][2].request.call_args.kwargs['headers']['If-None-Match']=='"a"'
    response.read1.assert_not_called()
    client,_,_=client_for([FakeResponse(status=304)])
    with pytest.raises(HTTPStatusError):client.get('https://news.example/article')


def test_conditional_validators_are_bound_to_the_exact_redirect_resource():
    responses=[FakeResponse(status=302,headers={'Location':'https://other.example/final'}),FakeResponse()]
    client,calls,_=client_for(responses)
    client.get('https://news.example/feed',conditional=Conditional('https://news.example/feed','"sensitive-etag"','Thu, 05 Nov 2026 12:00:00 GMT'))
    first=calls[0][2].request.call_args.kwargs['headers'];second=calls[1][2].request.call_args.kwargs['headers']
    assert 'If-None-Match' in first and 'If-None-Match' not in second and 'If-Modified-Since' not in second
    assert not {'Authorization','Cookie','Proxy-Authorization'}&set(second)


def test_validator_from_final_resource_is_not_sent_to_initial_redirector():
    client,calls,_=client_for([FakeResponse(status=302,headers={'Location':'https://other.example/final'}),FakeResponse(status=304)])
    client.get('https://news.example/feed',conditional=Conditional('https://other.example/final','"final"'),accept_not_modified=True)
    assert 'If-None-Match' not in calls[0][2].request.call_args.kwargs['headers']
    assert calls[1][2].request.call_args.kwargs['headers']['If-None-Match']=='"final"'

@pytest.mark.parametrize('etag',['bad\r\nAuthorization: injected','é','a'*1025,''])
def test_invalid_validator_rejected_before_any_connection(etag):
    with pytest.raises(FetchError):Conditional('https://news.example/feed',etag)


def test_conditional_request_cannot_follow_redirect_to_private_destination():
    from modules.http.external import BlockedURL
    client,calls,_=client_for([FakeResponse(status=302,headers={'Location':'http://169.254.169.254/latest'})])
    with pytest.raises(BlockedURL):client.get('https://news.example/feed',conditional=Conditional('https://news.example/feed','"a"'),accept_not_modified=True)
    assert len(calls)==1


def test_conditional_dns_rebinding_still_pins_the_validated_address():
    client,calls,resolver=client_for([FakeResponse(status=304)])
    resolver.side_effect=[[PUBLIC4],['127.0.0.1']]
    assert client.get('https://news.example/feed',conditional=Conditional('https://news.example/feed','"a"'),accept_not_modified=True).status_code==304
    assert resolver.call_count==1 and calls[0][1]==PUBLIC4


def test_redirected_429_protects_actual_destination_across_other_redirectors():
    from modules.http.external import HostBudget
    budget=HostBudget(clock=lambda:0)
    client,calls,_=client_for([
        FakeResponse(status=302,headers={'Location':'https://common.example/feed-a'}),
        FakeResponse(status=429,headers={'Retry-After':'7200'}),
        FakeResponse(status=302,headers={'Location':'https://common.example/feed-b'}),
    ])
    with pytest.raises(HTTPStatusError) as first:client.get('https://first.example/rss',host_budget=budget)
    with pytest.raises(HTTPStatusError) as second:client.get('https://second.example/rss',host_budget=budget)
    assert first.value.host==second.value.host=='common.example'
    assert float(second.value.retry_after)>=7200
    assert len(calls)==3 # The second connection to common.example was never opened.


def test_actual_host_semaphore_has_bounded_wait_and_recovers():
    import time
    from modules.http.external import HostBudget
    budget=HostBudget()
    with budget.slot('common.example',time.monotonic()+1):
        with pytest.raises(FetchError,match='deadline'):
            with budget.slot('common.example',time.monotonic()+.01):pass
    with budget.slot('common.example',time.monotonic()+1):pass
