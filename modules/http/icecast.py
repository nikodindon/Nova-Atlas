"""Separate policy for the explicitly configured internal Icecast origin.

Not usable for RSS/article URLs. No redirects, ambient proxy credentials or
caller-selected hosts/ports/paths. The continuous audio stream is intentionally
not subject to the finite document size limit of external sources.
"""
import ipaddress
import re

import requests

from modules.http.external import FetchError


def open_stream(host, port, mount, allowed_mounts):
    if not isinstance(host, str) or not host or len(host) > 253:
        raise FetchError('invalid_icecast_origin')
    try:
        addr = ipaddress.ip_address(host)
        authority = f'[{addr}]' if addr.version == 6 else str(addr)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
            raise FetchError('invalid_icecast_origin') from None
        authority = host
    if type(port) is not int or not 1 <= port <= 65535:
        raise FetchError('invalid_icecast_origin')
    if (not isinstance(mount, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', mount)
            or any(part in ('', '.', '..') for part in mount[1:].split('/'))
            or mount not in allowed_mounts):
        raise FetchError('invalid_icecast_mount')
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(f'http://{authority}:{port}{mount}', stream=True,
                               timeout=(5, 30), allow_redirects=False)
        if not 200 <= response.status_code < 300:
            response.close()
            raise FetchError('icecast_status')
        return response, session
    except Exception:
        session.close()
        raise FetchError('icecast_unavailable') from None
