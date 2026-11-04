"""Bounded HTTP(S) GET for untrusted sources, with DNS-to-socket IP pinning.

No environment proxies, cookies, netrc, authentication, pooling or automatic
redirects. The only network connect call takes a validated numeric address.
"""
from dataclasses import dataclass
import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import zlib


class FetchError(Exception):
    """Safe diagnostic code only: never include an untrusted URL or credentials."""


class BlockedURL(FetchError):
    pass


class HTTPStatusError(FetchError):
    """Structured status without URL, response body or credentials."""
    def __init__(self, status_code, retry_after=None):
        super().__init__('http_status')
        self.status_code = status_code
        self.retry_after = retry_after[:128] if isinstance(retry_after, str) else None


@dataclass(frozen=True)
class Limits:
    total_timeout: float = 30
    dns_timeout: float = 5
    io_timeout: float = 10
    max_bytes: int = 4 * 1024 * 1024
    max_redirects: int = 5

    def __post_init__(self):
        if not (0 < self.total_timeout <= 60 and 0 < self.dns_timeout <= 10
                and 0 < self.io_timeout <= 30 and 0 < self.max_bytes <= 8 * 1024 * 1024
                and 0 <= self.max_redirects <= 5):
            raise ValueError('Invalid HTTP limits')


@dataclass(frozen=True)
class Target:
    url: str
    host: str
    port: int
    secure: bool
    path: str

    @property
    def authority(self):
        # Ports are the standard port for the scheme, so omit them in Host.
        return f'[{self.host}]' if ':' in self.host else self.host


@dataclass(frozen=True)
class Response:
    content: bytes
    status_code: int
    headers: dict
    url: str

    @property
    def text(self):
        # RSS is passed as bytes to its XML parser. Article text honors HTTP charset.
        match = re.search(r'charset\s*=\s*[\"\']?([A-Za-z0-9._-]+)', self.headers.get('content-type', ''), re.I)
        encoding = match[1] if match else 'utf-8'
        try:
            return self.content.decode(encoding, errors='replace')
        except LookupError:
            return self.content.decode('utf-8', errors='replace')

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise HTTPStatusError(self.status_code, self.headers.get('retry-after'))


# Extra special ranges avoid ipaddress classification differences across Python releases.
_V4_SPECIAL = tuple(ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '100.64.0.0/10', '192.0.0.0/24', '192.88.99.0/24',
    '168.63.129.16/32',  # Azure platform/metadata virtual address, globally numbered.
))
_V6_GLOBAL = ipaddress.ip_network('2000::/3')
_V6_SPECIAL = tuple(ipaddress.ip_network(n) for n in (
    '2001::/23', '2002::/16', '3ffe::/16', '3fff::/20',
))


def public_address(value):
    if not isinstance(value, str) or '%' in value:
        raise BlockedURL('invalid_address')
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        raise BlockedURL('invalid_address') from None
    if (not addr.is_global or addr.is_multicast or addr.is_reserved
            or addr.is_loopback or addr.is_link_local or addr.is_unspecified):
        raise BlockedURL('non_public_address')
    if addr.version == 4 and any(addr in net for net in _V4_SPECIAL):
        raise BlockedURL('special_address')
    if addr.version == 6 and (addr not in _V6_GLOBAL or any(addr in net for net in _V6_SPECIAL)):
        # Reject mapped IPv4, NAT64, Teredo, 6to4 and other transition encodings.
        raise BlockedURL('special_address')
    return addr


def parse_url(url):
    """Pure validation, no DNS. Intended also for add/test/discovery entry points."""
    if (not isinstance(url, str) or not url or len(url) > 4096
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
            or '\\' in url or re.search(r'%(?![0-9A-Fa-f]{2})', url)):
        raise BlockedURL('invalid_url')
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.netloc or not parts.hostname
                or '@' in parts.netloc or '%' in parts.netloc):
            raise ValueError
        secure = parts.scheme == 'https'
        port = parts.port if parts.port is not None else (443 if secure else 80)
        if port != (443 if secure else 80) or parts.netloc.endswith(':'):
            raise ValueError
        host = parts.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is not None:
            public_address(str(addr))
            host = str(addr)
        else:
            # No inet_aton shorthand, integer/octal/hex IPv4 or search-domain names.
            if (re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*', host)
                    or len(host) > 253 or '.' not in host
                    or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home'))
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in host.split('.'))):
                raise ValueError
        authority = f'[{host}]' if ':' in host else host
        # Keep already encoded path/query; encode Unicode without normalizing credentials.
        path = quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
        canonical = urlunsplit((parts.scheme, authority, path, query, ''))
        if len(canonical) > 8192:
            raise ValueError
        return Target(canonical, host, port, secure, path + ('?' + query if query else ''))
    except (ValueError, UnicodeError):
        raise BlockedURL('invalid_url') from None


_DNS_SLOTS = threading.BoundedSemaphore(4)


def resolve(host, port, timeout):
    """Bound caller wait and outstanding system DNS calls; libc DNS itself isn't cancellable."""
    if not _DNS_SLOTS.acquire(blocking=False):
        raise FetchError('dns_busy')
    result = queue.Queue(maxsize=1)

    def worker():
        try:
            result.put(socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM))
        except Exception:
            result.put(None)
        finally:
            _DNS_SLOTS.release()

    try:
        threading.Thread(target=worker, daemon=True, name='SourceDNS').start()
    except Exception:
        _DNS_SLOTS.release()
        raise FetchError('dns_busy') from None
    try:
        answer = result.get(timeout=timeout)
    except queue.Empty:
        raise FetchError('dns_timeout') from None
    if not answer:
        raise FetchError('dns_error')
    return [row[4][0] for row in answer if row[0] in (socket.AF_INET, socket.AF_INET6)]


def remaining(deadline):
    duration = deadline - time.monotonic()
    if duration <= 0:
        raise FetchError('deadline')
    return duration


class _LineBudgetReader:
    """Bound headers/status lines and chunk framing, in addition to the body budget."""
    def __init__(self, stream):
        self.stream = stream
        self.used = 0

    def readline(self, limit=-1):
        line = self.stream.readline(min(limit, 8193) if limit >= 0 else 8193)
        self.used += len(line)
        if len(line) > 8192 or self.used > 65536:
            raise FetchError('headers_too_large')
        return line

    def __getattr__(self, name):
        return getattr(self.stream, name)


class _BoundedResponse(http.client.HTTPResponse):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fp = _LineBudgetReader(self.fp)


class PinnedConnection(http.client.HTTPConnection):
    """HTTP parsing from stdlib; socket connection never resolves the hostname again."""
    def __init__(self, target, address, limits, deadline):
        super().__init__(target.host, target.port, timeout=limits.io_timeout)
        self.response_class = _BoundedResponse
        self.target = target
        self.address = address
        self.limits = limits
        self.deadline = deadline
        self.aborted = threading.Event()
        self.transport_socket = None

    def abort(self):
        self.aborted.set()
        sock = self.transport_socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def connect(self):
        address = public_address(self.address)  # Defense at the actual connection boundary.
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.transport_socket = self.sock
        try:
            self.sock.settimeout(min(self.limits.io_timeout, remaining(self.deadline)))
            if self.aborted.is_set():
                raise FetchError('deadline')
            endpoint = (str(address), self.target.port) if family == socket.AF_INET else (str(address), self.target.port, 0, 0)
            self.sock.connect(endpoint)
            if ipaddress.ip_address(self.sock.getpeername()[0]) != address:
                raise BlockedURL('peer_mismatch')
            if self.target.secure:
                context = ssl.create_default_context()
                # Store the wrapped socket before the handshake, so the deadline can interrupt it.
                self.sock = context.wrap_socket(self.sock, server_hostname=self.target.host,
                                                do_handshake_on_connect=False)
                self.transport_socket = self.sock
                self.sock.settimeout(min(self.limits.io_timeout, remaining(self.deadline)))
                if self.aborted.is_set():
                    raise FetchError('deadline')
                self.sock.do_handshake()
            remaining(self.deadline)
        except BaseException:
            self.close()
            raise


def _read_body(response, limits, deadline):
    headers = {key.lower(): value for key, value in response.getheaders()}
    length = headers.get('content-length')
    if length is not None:
        if not re.fullmatch('[0-9]+', length) or int(length) > limits.max_bytes:
            raise FetchError('response_too_large')
    encoding = headers.get('content-encoding', 'identity').lower().strip()
    if encoding not in ('identity', 'gzip'):
        raise FetchError('unsupported_encoding')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else None
    body = bytearray()
    wire_size = 0
    while True:
        remaining(deadline)
        chunk = response.read1(min(65536, limits.max_bytes - wire_size + 1))
        if not chunk:
            break
        wire_size += len(chunk)
        if wire_size > limits.max_bytes:
            raise FetchError('response_too_large')
        if decoder:
            chunk = decoder.decompress(chunk, limits.max_bytes - len(body) + 1)
            if len(body) + len(chunk) > limits.max_bytes:
                raise FetchError('response_too_large')
            if decoder.unconsumed_tail or decoder.unused_data:
                raise FetchError('invalid_or_large_compression')
        body.extend(chunk)
        if len(body) > limits.max_bytes:
            raise FetchError('response_too_large')
    if decoder and not decoder.eof:
        raise FetchError('invalid_compression')
    if length is not None and 'transfer-encoding' not in headers and wire_size != int(length):
        raise FetchError('truncated_response')
    remaining(deadline)
    return bytes(body), headers


class ExternalHTTPClient:
    def __init__(self, limits=None, *, resolver=None, connection_factory=None):
        self.limits = limits or Limits()
        # Injection is for offline tests, never selected from URLs/environment/configuration.
        self.resolver = resolver or resolve
        self.connection_factory = connection_factory or PinnedConnection

    def get(self, url):
        deadline = time.monotonic() + self.limits.total_timeout
        visited = set()
        try:
            for hop in range(self.limits.max_redirects + 1):
                target = parse_url(url)
                if target.url in visited:
                    raise FetchError('redirect_loop')
                visited.add(target.url)
                try:
                    addresses = [str(ipaddress.ip_address(target.host))]
                except ValueError:
                    addresses = self.resolver(target.host, target.port,
                                              min(self.limits.dns_timeout, remaining(deadline)))
                if not addresses or len(addresses) > 64:
                    raise BlockedURL('invalid_dns_answer')
                # Fail closed for mixed public/private answers; never just filter the private ones.
                addresses = list(dict.fromkeys(str(public_address(value)) for value in addresses))
                remaining(deadline)
                connection = self.connection_factory(target, addresses[0], self.limits, deadline)
                watchdog = threading.Timer(remaining(deadline), connection.abort)
                watchdog.daemon = True
                watchdog.start()
                response = None
                try:
                    connection.request('GET', target.path, headers={
                        'Host': target.authority, 'User-Agent': 'Mozilla/5.0 (compatible; AtlasNews/2.0)',
                        'Accept': 'application/rss+xml, application/atom+xml, text/html, */*;q=0.8',
                        'Accept-Encoding': 'gzip, identity', 'Connection': 'close',
                    })
                    response = connection.getresponse()
                    headers = {key.lower(): value for key, value in response.getheaders()}
                    if response.status in (301, 302, 303, 307, 308):
                        location = headers.get('location')
                        if not location or hop >= self.limits.max_redirects:
                            raise FetchError('redirect_limit')
                        if len(location) > 4096 or any(c.isspace() or ord(c) < 32 for c in location):
                            raise BlockedURL('invalid_redirect')
                        url = urljoin(target.url, location)
                        if target.secure and urlsplit(url).scheme == 'http':
                            raise BlockedURL('https_downgrade')
                        continue
                    if not 200 <= response.status < 300:
                        raise HTTPStatusError(response.status, headers.get('retry-after'))
                    content, headers = _read_body(response, self.limits, deadline)
                    return Response(content, response.status, headers, target.url)
                finally:
                    watchdog.cancel()
                    if response is not None:
                        response.close()
                    connection.close()
        except FetchError:
            raise
        except (OSError, ValueError, http.client.HTTPException, zlib.error):
            raise FetchError('transport_error') from None
        raise FetchError('redirect_limit')


def get(url, *, timeout=30):
    """Shared entry point for fetch/test/discovery. No header/auth/proxy escape hatches."""
    return ExternalHTTPClient(Limits(total_timeout=timeout, io_timeout=min(timeout, 10))).get(url)
