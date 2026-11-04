"""Context-aware HTML rendering and response policy for the legacy frontend."""
import os
import re
from urllib.parse import urlsplit
import markdown
import nh3
from jinja2 import Environment
from markupsafe import Markup


def safe_url(value):
    """External article/source links: absolute HTTP(S), no credentials or controls."""
    if not isinstance(value, str) or not value or re.search(r'[\s\\\x00-\x1f\x7f]', value):
        return '#'
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None):
            return '#'
        _ = parts.port
        return value
    except ValueError:
        return '#'


_HTML = Environment(autoescape=True)
_HTML.filters['safe_url'] = safe_url


def html_template(source):
    return _HTML.from_string(source)


_MARKDOWN_TAGS = {'p','br','hr','strong','em','b','i','del','s','blockquote','pre','code',
                  'h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody',
                  'tr','th','td','a','sup','sub','dl','dt','dd'}


def render_markdown(text):
    html = markdown.markdown(text, extensions=['extra'])
    def attribute(tag, key, value):
        if tag == 'a' and key == 'href':
            return value if safe_url(value) != '#' else None
        return value
    return Markup(nh3.clean(html, tags=_MARKDOWN_TAGS,
        clean_content_tags={'script','style','iframe','object','svg','math','template'},
        attributes={'a': {'href', 'title'}, 'ol': {'start'}}, attribute_filter=attribute,
        url_schemes={'http','https'}, url_relative='deny', link_rel='noopener noreferrer'))


CSP = ("default-src 'self'; script-src 'self'; script-src-attr 'none'; "
       "style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; "
       "connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'self'; "
       "frame-src 'none'; frame-ancestors 'none'; worker-src 'none'; form-action 'self'")


def install_web_security(app):
    from flask import request
    try:
        hsts = max(0, min(31536000, int(os.environ.get('ATLAS_HSTS_MAX_AGE', '0'))))
    except ValueError:
        hsts = 0

    @app.after_request
    def security_headers(response):
        response.headers['Content-Security-Policy'] = CSP
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), payment=()'
        if hsts and request.is_secure:
            response.headers['Strict-Transport-Security'] = f'max-age={hsts}'
        return response
