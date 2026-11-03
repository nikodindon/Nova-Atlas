"""No network: DNS answers, HTTP transport, sockets and TLS are all simulated."""
import gzip
import io
import socket
import ssl
import time
from unittest.mock import Mock

import pytest

from modules.http.external import (
    BlockedURL, ExternalHTTPClient, FetchError, Limits, PinnedConnection,
    Response, parse_url, public_address, resolve,
)

from modules.fetch.atlas_fetch import ArticleFetcher

PUBLIC4 = '93.184.216.34'
PUBLIC6 = '2606:4700:4700::1111'


class FakeResponse:
    def __init__(self, body=b'<rss/>', status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.stream = io.BytesIO(body)
        self.close = Mock()

    def getheaders(self):
        return list(self.headers.items())

    def read1(self, size):
        return self.stream.read(size)


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.request = Mock()
        self.close = Mock()
        self.abort = Mock()

    def getresponse(self):
        return self.response


def client_for(responses, *, addresses=None, limits=None):
    connections = []
    resolver = Mock(return_value=addresses or [PUBLIC4])
    def factory(target, address, policy, deadline):
        connection = FakeConnection(responses[len(connections)])
        connections.append((target, address, connection))
        return connection
    return ExternalHTTPClient(limits, resolver=resolver, connection_factory=factory), connections, resolver


@pytest.mark.parametrize('url', ['http://news.example/feed', 'https://news.example/feed',
                                 f'http://{PUBLIC4}/feed', f'https://[{PUBLIC6}]/feed'])
def test_public_http_and_https(url):
    client, calls, dns = client_for([FakeResponse()])
    result = client.get(url)
    assert result.content == b'<rss/>' and result.status_code == 200
    result.raise_for_status()
    target, address, connection = calls[0]
    assert address in (PUBLIC4, PUBLIC6)
    assert connection.request.call_args.args == ('GET', '/feed')
    assert connection.request.call_args.kwargs['headers']['Host'] == target.authority
    connection.close.assert_called_once()


@pytest.mark.parametrize('address', [
    '0.0.0.0', '0.1.2.3', '127.0.0.1', '127.12.3.4', '10.1.2.3', '172.17.0.1',
    '172.31.255.254', '192.168.1.1', '169.254.169.254', '169.254.170.2',
    '100.64.0.1', '100.91.114.49', '100.127.255.254', '100.100.100.200',
    '192.0.0.8', '192.0.0.9', '192.88.99.1', '168.63.129.16', '192.0.2.1',
    '198.18.0.1', '198.51.100.1', '203.0.113.1', '224.0.0.1', '240.0.0.1', '255.255.255.255',
    '::', '::1', 'fc00::1', 'fd00:ec2::254', 'fe80::1', 'fec0::1', 'ff02::1',
    '::ffff:127.0.0.1', '::ffff:10.0.0.1', '::ffff:8.8.8.8', '::127.0.0.1',
    '64:ff9b::7f00:1', '64:ff9b:1::a00:1', '2001:db8::1', '2002:7f00:1::1', '2001::1', '2606:4700::1%eth0', '3ffe::1', '3fff::1',
])
def test_forbidden_address_never_reaches_transport(address):
    client, calls, _ = client_for([FakeResponse()], addresses=[address])
    with pytest.raises(BlockedURL):
        client.get('https://news.example/feed')
    assert calls == []
    with pytest.raises(BlockedURL):
        public_address(address)


@pytest.mark.parametrize('url', [
    'file:///etc/passwd', 'ftp://news.example/a', 'gopher://news.example/',
    'http://localhost/', 'http://localhost./', 'http://foo.localhost/', 'http://docker/',
    'http://host.local/', 'http://metadata.google.internal/', 'http://127.1/',
    'http://2130706433/', 'http://0177.0.0.1/', 'http://0x7f000001/', 'http://0x7f.0.0.1/',
    'http://127.000.000.001/', 'http://１２７.０.０.１/', 'http://[::ffff:127.0.0.1]/',
    'http://[fe80::1%25eth0]/', 'http://user:secret@news.example/', 'http://news.example@127.0.0.1/',
    'http://news.example:22/', 'https://news.example:80/', 'http://news.example:8080/',
    'http://news.example:0/', 'http://news.example:-1/', 'http://news.example:65536/',
    'http://news.example:/', 'http://news.example\\@127.0.0.1/', 'http://%31%32%37.0.0.1/',
    'http:///path', '//news.example/a', 'http:///', 'http://[not-ip]/',
    'http://news.example/\nHost:evil', ' http://news.example/', 'http://news.example/a%zz',
    'http://news.example/\x00', 'http://news.example/' + 'a'*4096,
])
def test_malformed_atypical_and_unsafe_urls(url):
    client, calls, dns = client_for([FakeResponse()])
    with pytest.raises(BlockedURL):
        client.get(url)
    assert calls == []
    dns.assert_not_called()


@pytest.mark.parametrize('addresses', [[PUBLIC4, '127.0.0.1'], [PUBLIC6, '172.17.0.1'],
                                      ['100.64.1.1', PUBLIC4]])
def test_any_forbidden_dns_answer_rejects_whole_result(addresses):
    client, calls, dns = client_for([FakeResponse()], addresses=addresses)
    with pytest.raises(BlockedURL):
        client.get('https://news.example/')
    assert not calls


def test_idna_unicode_path_and_query_preserved():
    target = parse_url('https://münich.example/actualité?q=France&hl=fr#section')
    assert target.host == 'xn--mnich-kva.example'
    assert target.path == '/actualit%C3%A9?q=France&hl=fr'
    assert '#' not in target.url


def test_dns_rebinding_does_not_resolve_again_at_connect(monkeypatch):
    answers = Mock(side_effect=[[PUBLIC4], ['127.0.0.1']])
    sock = Mock()
    sock.getpeername.return_value = (PUBLIC4, 443)
    socket_factory = Mock(return_value=sock)
    monkeypatch.setattr('socket.socket', socket_factory)
    monkeypatch.setattr('socket.getaddrinfo', Mock(side_effect=AssertionError('Independent resolution forbidden')))
    class Wire(PinnedConnection):
        def request(self, *args, **kwargs):
            self.connect()
        def getresponse(self):
            return FakeResponse()
    client = ExternalHTTPClient(resolver=answers, connection_factory=Wire)
    assert client.get('http://changing.example/').content == b'<rss/>'
    answers.assert_called_once()
    sock.connect.assert_called_once_with((PUBLIC4, 80))
    socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)


@pytest.mark.parametrize('scheme,expected_port', [('http', 80), ('https', 443)])
def test_exact_ip_host_sni_and_tls_verification(monkeypatch, scheme, expected_port):
    sock = Mock()
    sock.getpeername.return_value = (PUBLIC6, expected_port, 0, 0)
    monkeypatch.setattr('socket.socket', Mock(return_value=sock))
    context = ssl.create_default_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    wrapped = Mock()
    wrapper = Mock(return_value=wrapped)
    monkeypatch.setattr(context, 'wrap_socket', wrapper)
    context_factory = Mock(return_value=context)
    monkeypatch.setattr('ssl.create_default_context', context_factory)
    conn = PinnedConnection(parse_url(f'{scheme}://news.example/'), PUBLIC6, Limits(), time.monotonic()+30)
    conn.connect()
    sock.connect.assert_called_once_with((PUBLIC6, expected_port, 0, 0))
    if scheme == 'https':
        wrapper.assert_called_once_with(sock, server_hostname='news.example', do_handshake_on_connect=False)
        wrapped.do_handshake.assert_called_once()
    else:
        context_factory.assert_not_called()
    conn.close()


def test_peer_address_mismatch_rejected(monkeypatch):
    sock = Mock()
    sock.getpeername.return_value = ('127.0.0.1', 80)
    monkeypatch.setattr('socket.socket', Mock(return_value=sock))
    conn = PinnedConnection(parse_url('http://news.example/'), PUBLIC4, Limits(), time.monotonic()+30)
    with pytest.raises(BlockedURL, match='peer_mismatch'):
        conn.connect()
    sock.close.assert_called_once()


def test_timer_can_abort_socket_even_after_http_connection_detaches_it(monkeypatch):
    sock = Mock()
    sock.getpeername.return_value = (PUBLIC4, 80)
    monkeypatch.setattr('socket.socket', Mock(return_value=sock))
    conn = PinnedConnection(parse_url('http://news.example/'), PUBLIC4, Limits(), time.monotonic()+30)
    conn.connect()
    # http.client.getresponse closes/detaches self.sock for Connection: close,
    # while HTTPResponse's makefile still owns the live descriptor.
    conn.close()
    assert conn.sock is None
    conn.abort()
    sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)


@pytest.mark.parametrize('error', [socket.timeout('synthetic-timeout'), ssl.SSLCertVerificationError('synthetic-secret'), OSError('synthetic-secret')])
def test_network_and_tls_failure_are_sanitized_and_closed(error):
    client, calls, _ = client_for([FakeResponse()])
    original = client.connection_factory
    def factory(*args):
        conn = original(*args)
        conn.request.side_effect = error
        return conn
    client.connection_factory = factory
    with pytest.raises(FetchError, match='transport_error') as captured:
        client.get('https://news.example/')
    assert 'synthetic-secret' not in str(captured.value)
    calls[0][2].close.assert_called_once()


@pytest.mark.parametrize('location', ['http://127.0.0.1/admin', 'http://172.17.0.1/',
                                      'http://100.64.0.1/', 'http://[::1]/', '//169.254.169.254/latest/meta-data/'])
def test_redirect_to_private_address_is_not_contacted(location):
    client, calls, _ = client_for([FakeResponse(status=302, headers={'Location': location})])
    with pytest.raises(BlockedURL):
        client.get('http://news.example/')
    assert len(calls) == 1
    calls[0][2].close.assert_called_once()


def test_redirect_target_is_resolved_and_validated_again():
    client, calls, dns = client_for([FakeResponse(status=302, headers={'Location': 'https://other.example/'})])
    dns.side_effect = [[PUBLIC4], ['192.168.1.1']]
    with pytest.raises(BlockedURL):
        client.get('https://news.example/')
    assert len(calls) == 1 and dns.call_count == 2


def test_public_redirect_chain_and_no_ambient_credentials(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy.invalid/')
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.invalid/')
    monkeypatch.setenv('ALL_PROXY', 'http://proxy.invalid/')
    client, calls, dns = client_for([
        FakeResponse(status=301, headers={'Location': '/new', 'Set-Cookie': 'secret=do-not-forward'}),
        FakeResponse(status=307, headers={'Location': 'https://other.example/end'}),
        FakeResponse(b'news'),
    ])
    result = client.get('https://news.example/start')
    assert result.content == b'news' and result.url == 'https://other.example/end'
    assert dns.call_count == 3
    for _, _, conn in calls:
        headers = conn.request.call_args.kwargs['headers']
        assert not {'Authorization', 'Cookie', 'Proxy-Authorization'} & headers.keys()
        assert conn.request.call_args.args[0] == 'GET'
        conn.close.assert_called_once()
    assert calls[-1][2].request.call_args.kwargs['headers']['Host'] == 'other.example'


@pytest.mark.parametrize('responses,reason', [
    ([FakeResponse(status=302, headers={'Location': '/'})], 'redirect_loop'),
    ([FakeResponse(status=302, headers={'Location': '/next'})], 'redirect_limit'),
])
def test_loops_and_redirect_limits(responses, reason):
    client, calls, _ = client_for(responses, limits=Limits(max_redirects=0 if reason == 'redirect_limit' else 5))
    with pytest.raises(FetchError, match=reason):
        client.get('https://news.example/')
    assert len(calls) == 1


def test_redirect_chain_bounded_to_five():
    responses = [FakeResponse(status=302, headers={'Location': f'/{i}'}) for i in range(6)]
    client, calls, _ = client_for(responses)
    with pytest.raises(FetchError, match='redirect_limit'):
        client.get('https://news.example/start')
    assert len(calls) == 6  # initial request + five redirects, never a seventh.


def test_https_downgrade_disallowed():
    client, calls, _ = client_for([FakeResponse(status=302, headers={'Location': 'http://news.example/'})])
    with pytest.raises(BlockedURL, match='https_downgrade'):
        client.get('https://news.example/')
    assert len(calls) == 1


@pytest.mark.parametrize('body,headers,reason', [
    (b'x'*65, {}, 'response_too_large'),
    (b'', {'Content-Length': '65'}, 'response_too_large'),
    (b'', {'Content-Length': '-1'}, 'response_too_large'),
    (gzip.compress(b'x'*1000), {'Content-Encoding': 'gzip'}, 'response_too_large'),
    (b'not-gzip', {'Content-Encoding': 'gzip'}, 'transport_error'),
    (b'x', {'Content-Encoding': 'br'}, 'unsupported_encoding'),
    (b'x', {'Content-Length': '10'}, 'truncated_response'),
    (gzip.compress(b'x')[:-3], {'Content-Encoding': 'gzip'}, 'invalid_compression'),
])
def test_response_limits_and_compression(body, headers, reason):
    client, calls, _ = client_for([FakeResponse(body, headers=headers)], limits=Limits(max_bytes=64))
    with pytest.raises(FetchError, match=reason):
        client.get('https://news.example/')
    calls[0][2].close.assert_called_once()


def test_valid_gzip_and_http_charset():
    body = '<p>actualité française</p>'.encode('iso-8859-1')
    client, _, _ = client_for([FakeResponse(gzip.compress(body), headers={
        'Content-Encoding': 'gzip', 'Content-Type': 'text/html; charset=iso-8859-1'})])
    assert client.get('https://news.example/').text == '<p>actualité française</p>'


def test_body_read_timeout_is_closed():
    response = FakeResponse()
    response.read1 = Mock(side_effect=socket.timeout())
    client, calls, _ = client_for([response])
    with pytest.raises(FetchError, match='transport_error'):
        client.get('https://news.example/')
    calls[0][2].close.assert_called_once()


def test_total_deadline_includes_dns_work():
    client, calls, dns = client_for([FakeResponse()])
    def elapsed(*args):
        # Deterministic monotonic clock: no sleeping or real DNS.
        clock.return_value = 100
        return [PUBLIC4]
    clock = Mock(return_value=0)
    from unittest.mock import patch
    with patch('modules.http.external.time.monotonic', clock):
        dns.side_effect = elapsed
        with pytest.raises(FetchError, match='deadline'):
            client.get('https://news.example/')
    assert not calls


def test_dns_resolver_uses_only_stream_answers(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', Mock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC4, 80)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, '', (PUBLIC6, 80, 0, 0)),
    ]))
    assert resolve('news.example', 80, 1) == [PUBLIC4, PUBLIC6]


def test_dns_errors_do_not_disclose_names_or_credentials(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', Mock(side_effect=OSError('synthetic-private-secret')))
    with pytest.raises(FetchError, match='dns_error') as captured:
        resolve('news.example', 80, 1)
    assert 'synthetic-private-secret' not in str(captured.value)


def test_existing_public_feeds_have_supported_url_shape():
    from modules.core.feeds_loader import load_feeds
    root = __import__('pathlib').Path(__file__).resolve().parent.parent
    feeds = load_feeds(root / 'config' / 'feeds.yaml')
    urls = [url for group in feeds.values() for url in group]
    assert len(urls) == 120
    for url in urls:
        target = parse_url(url)
        assert target.port in (80, 443)


def test_rss_and_article_share_same_client(monkeypatch):
    from modules.fetch.atlas_fetch import ArticleFetcher
    fetcher = ArticleFetcher.__new__(ArticleFetcher)
    fetcher.max_per_feed = 8
    fetcher.log = Mock()
    fetch = Mock(side_effect=[
        Response(b'<rss><channel><item><title>News</title><link>https://article.example/news</link></item></channel></rss>', 200, {}, 'https://feed.example/'),
        Response(('<article>' + 'Information synthétique. '*20 + '</article>').encode(), 200, {}, 'https://article.example/news'),
    ])
    monkeypatch.setattr('modules.fetch.atlas_fetch.source_http.get', fetch)
    assert fetcher._fetch_rss('https://feed.example/')[0]['title'] == 'News'
    assert 'Information synthétique' in fetcher._fetch_content('https://article.example/news')
    assert fetch.call_count == 2


def test_blocked_article_fails_without_llm_or_disk_writes(monkeypatch):
    from modules.fetch.atlas_fetch import ArticleFetcher
    fetcher = ArticleFetcher.__new__(ArticleFetcher)
    fetcher.log = Mock()
    fetcher.max_per_feed = 8
    assert fetcher._fetch_rss('http://127.0.0.1/feed') == []
    assert fetcher._fetch_content('http://100.64.1.2/article') == ''
    assert '127.0.0.1' not in str(fetcher.log.mock_calls)


class WireSocket:
    """Socket substitute exercising the real http.client request/response parser."""
    def __init__(self, wire):
        self.wire = io.BytesIO(wire)
        self.sent = bytearray()
        self.connect = Mock()
        self.settimeout = Mock()
        self.shutdown = Mock()
        self.close = Mock()
        self.do_handshake = Mock()

    def getpeername(self):
        return (PUBLIC4, 80)

    def sendall(self, data):
        self.sent.extend(data)

    def makefile(self, *args):
        return self.wire


def wire_client(monkeypatch, wire):
    sock = WireSocket(wire)
    monkeypatch.setattr('socket.socket', Mock(return_value=sock))
    return ExternalHTTPClient(resolver=Mock(return_value=[PUBLIC4])), sock


def test_real_http_parser_and_direct_socket_path(monkeypatch):
    client, sock = wire_client(monkeypatch, b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nnews')
    assert client.get('http://news.example/feed?q=France').content == b'news'
    assert b'GET /feed?q=France HTTP/1.1\r\n' in sock.sent
    assert b'Host: news.example\r\n' in sock.sent
    assert b'Authorization:' not in sock.sent and b'Cookie:' not in sock.sent
    sock.connect.assert_called_once_with((PUBLIC4, 80))


def test_real_http_parser_chunked_body(monkeypatch):
    client, sock = wire_client(monkeypatch, b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nnews\r\n0\r\n\r\n')
    assert client.get('http://news.example/feed').content == b'news'


@pytest.mark.parametrize('headers', [b'X-Large: ' + b'x'*8192 + b'\r\n',
                                     (b'X-Header: ' + b'x'*7000 + b'\r\n')*10])
def test_header_size_budget_with_real_parser(monkeypatch, headers):
    client, sock = wire_client(monkeypatch, b'HTTP/1.1 200 OK\r\n'+headers+b'\r\n')
    with pytest.raises(FetchError, match='headers_too_large'):
        client.get('http://news.example/feed')
    sock.close.assert_called()


def test_oversized_chunked_body_with_real_parser(monkeypatch):
    body = b'x'*65
    client, sock = wire_client(monkeypatch, b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n41\r\n'+body+b'\r\n0\r\n\r\n')
    client.limits = Limits(max_bytes=64)
    with pytest.raises(FetchError, match='response_too_large'):
        client.get('http://news.example/feed')


def test_slow_header_total_deadline_aborts_transport(monkeypatch):
    # Fire the real watchdog callback during header parsing, no sleeping/network.
    client, sock = wire_client(monkeypatch, b'')
    timer = Mock()
    timer_factory = Mock(return_value=timer)
    monkeypatch.setattr('modules.http.external.threading.Timer', timer_factory)
    class HeaderReader(io.BytesIO):
        def readline(self, *args):
            timer_factory.call_args.args[1]()
            raise OSError('header read interrupted by shutdown')
    sock.wire = HeaderReader()
    with pytest.raises(FetchError, match='transport_error'):
        client.get('http://news.example/feed')
    timer.cancel.assert_called_once()
    sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)


def test_dns_wait_is_bounded_and_busy_slots_do_not_spawn_threads(monkeypatch):
    import modules.http.external as external
    thread = Mock()  # Suppress worker; Queue.get must time out without network.
    monkeypatch.setattr(external.threading, 'Thread', thread)
    semaphore = Mock()
    semaphore.acquire.return_value = False
    monkeypatch.setattr(external, '_DNS_SLOTS', semaphore)
    with pytest.raises(FetchError, match='dns_busy'):
        resolve('news.example', 80, 0.01)
    thread.assert_not_called()
    semaphore.acquire.return_value = True
    with pytest.raises(FetchError, match='dns_timeout'):
        resolve('news.example', 80, 0.001)
    thread.return_value.start.assert_called_once()
    # Slot remains occupied until the outstanding libc lookup actually finishes.
    semaphore.release.assert_not_called()


def test_same_host_redirect_cannot_rebind(monkeypatch):
    client, calls, dns = client_for([FakeResponse(status=302, headers={'Location': '/other'})])
    dns.side_effect = [[PUBLIC4], ['127.0.0.1']]
    with pytest.raises(BlockedURL):
        client.get('https://news.example/')
    assert len(calls) == 1


def test_internal_icecast_is_separate_and_no_proxy_or_redirect(monkeypatch):
    from modules.http.icecast import open_stream
    session = Mock()
    response = Mock(status_code=200)
    session.get.return_value = response
    monkeypatch.setattr('requests.Session', Mock(return_value=session))
    result, returned_session = open_stream('127.0.0.1', 8000, '/nova', {'/nova'})
    assert result is response and returned_session is session
    assert session.trust_env is False
    session.get.assert_called_once_with('http://127.0.0.1:8000/nova', stream=True, timeout=(5, 30), allow_redirects=False)


@pytest.mark.parametrize('host', ['icecast', '172.17.0.2', '100.91.114.49', '::1'])
def test_trusted_icecast_internal_hosts_are_preserved(monkeypatch, host):
    from modules.http.icecast import open_stream
    session = Mock()
    session.get.return_value.status_code = 200
    monkeypatch.setattr('requests.Session', Mock(return_value=session))
    response, _ = open_stream(host, 8000, '/nova-android', {'/nova', '/nova-android'})
    assert response.status_code == 200


@pytest.mark.parametrize('mount', ['/admin/', '//169.254.169.254', '/nova?target=secret', '/nova/../../admin', '/other', '/nova%2f..'])
def test_icecast_caller_cannot_choose_other_paths(monkeypatch, mount):
    from modules.http.icecast import open_stream
    factory = Mock()
    monkeypatch.setattr('requests.Session', factory)
    with pytest.raises(FetchError, match='invalid_icecast_mount'):
        open_stream('127.0.0.1', 8000, mount, {'/nova'})
    factory.assert_not_called()


def test_icecast_redirect_is_not_followed(monkeypatch):
    from modules.http.icecast import open_stream
    session = Mock()
    session.get.return_value.status_code = 302
    monkeypatch.setattr('requests.Session', Mock(return_value=session))
    with pytest.raises(FetchError, match='icecast_unavailable'):
        open_stream('127.0.0.1', 8000, '/nova', {'/nova'})
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs['allow_redirects'] is False
    session.close.assert_called_once()
    session.get.return_value.close.assert_called_once()


def test_rss_xml_cannot_load_external_dtd_or_entities(monkeypatch, tmp_path):
    from lxml import etree
    from modules.fetch import atlas_fetch
    calls = []
    class NoExternalResources(etree.Resolver):
        def resolve(self, url, public_id, context):
            calls.append(url)
            raise AssertionError('No XML resource may be loaded')
    factory = atlas_fetch._rss_xml_parser
    def guarded_factory(**kwargs):
        parser = factory(**kwargs)
        parser.resolvers.add(NoExternalResources())
        return parser
    monkeypatch.setattr(atlas_fetch, '_rss_xml_parser', guarded_factory)
    synthetic = tmp_path / 'synthetic.txt'
    synthetic.write_text('synthetic-credential-not-to-read')
    xml = (f'<!DOCTYPE rss SYSTEM "http://127.0.0.1/external.dtd" '
           f'[<!ENTITY local SYSTEM "{synthetic.as_uri()}">]>'
           '<rss><channel><item><title>&local;</title><link>https://news.example/</link></item></channel></rss>').encode()
    monkeypatch.setattr(atlas_fetch.source_http, 'get', Mock(return_value=Response(xml, 200, {}, 'https://news.example/')))
    fetcher = ArticleFetcher.__new__(ArticleFetcher)
    fetcher.max_per_feed = 8
    fetcher.log = Mock()
    result = fetcher._fetch_rss('https://news.example/')
    assert calls == []
    assert 'synthetic-credential-not-to-read' not in str(result)


def test_exact_configured_icecast_mount_with_extension_and_directory(monkeypatch):
    from modules.http.icecast import open_stream
    session = Mock()
    session.get.return_value.status_code = 200
    monkeypatch.setattr('requests.Session', Mock(return_value=session))
    response, _ = open_stream('icecast', 8000, '/radio/live.mp3', {'/radio/live.mp3'})
    assert response.status_code == 200
    assert session.get.call_args.args == ('http://icecast:8000/radio/live.mp3',)
