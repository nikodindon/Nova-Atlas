"""Single-admin authentication for the existing, single-process Flask web service."""
from collections import deque
from datetime import timedelta
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time

from flask import jsonify, redirect, render_template_string, request, session
from flask.sessions import SecureCookieSessionInterface
from werkzeug.security import check_password_hash


class SecureCookies(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        return app.config['SESSION_COOKIE_SECURE'] or request.is_secure


class TrustedProxy:
    """Only the explicitly allowed immediate peer can assert HTTPS. Never trust identity headers."""
    def __init__(self, app, networks):
        self.app = app
        self.networks = [ipaddress.ip_network(n.strip()) for n in networks.split(',') if n.strip()]

    def __call__(self, environ, start_response):
        try:
            peer = ipaddress.ip_address(environ.get('REMOTE_ADDR', ''))
            trusted = any(peer in n for n in self.networks)
        except ValueError:
            trusted = False
        if trusted and environ.get('HTTP_X_FORWARDED_PROTO') == 'https':
            environ['wsgi.url_scheme'] = 'https'
        # Forwarded host, client IP and identity are deliberately unused.
        return self.app(environ, start_response)


LOGIN = '''<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Nova Atlas — Connexion</title><style>
body{background:#0d1014;color:#d4d4d4;font:16px system-ui;max-width:420px;margin:12vh auto;padding:20px}
form{display:grid;gap:16px;background:#171c24;padding:24px;border-radius:12px}
input,button{font:inherit;padding:12px;border-radius:6px}button{background:#e8612a;color:white;border:0}
a{color:#6ea8fe}</style></head><body><h1>Nova Atlas</h1><h2>Administration</h2>
<p>{{ message }}</p><form method="post" action="/admin/login">
<input type="hidden" name="csrf_token" value="{{ csrf }}">
<label>Identifiant <input name="username" autocomplete="username" required maxlength="128"></label>
<label>Mot de passe <input type="password" name="password" autocomplete="current-password" required maxlength="1024"></label>
<button>Se connecter</button></form><p><a href="/">Retour aux actualités</a></p></body></html>'''

# Loaded before page initializers; tokens remain confined to private views.
BROWSER_CSRF = '<script src="/assets/csrf.js"></script>'


def install_admin_security(app):
    username = os.environ.get('ATLAS_ADMIN_USERNAME', '')
    password_hash = os.environ.get('ATLAS_ADMIN_PASSWORD_HASH', '')
    signing_key = os.environ.get('ATLAS_SESSION_SECRET', '')
    # Accept only our documented, bounded scrypt parameters. No plaintext/weak hashes.
    configured = bool(username and len(username) <= 128 and len(signing_key) >= 32
                      and re.fullmatch(r'scrypt:32768:8:1\$[A-Za-z0-9]{16,}\$[0-9a-f]{128}', password_hash))
    app.secret_key = signing_key if configured else secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_NAME='atlas_admin', SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.environ.get('ATLAS_COOKIE_SECURE', '1') != '0',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=1),
                      MAX_CONTENT_LENGTH=65536)
    app.session_interface = SecureCookies()
    app.wsgi_app = TrustedProxy(app.wsgi_app, os.environ.get('ATLAS_TRUSTED_PROXIES', ''))
    sessions = {}
    failures = deque(maxlen=10)
    lock = threading.RLock()
    costly = {'api_create_flash': deque(maxlen=5), 'config_restart': deque(maxlen=5)}
    app.extensions['atlas_admin'] = {'sessions': sessions, 'clock': time.monotonic}
    clock = lambda: app.extensions['atlas_admin']['clock']()

    def csrf():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_urlsafe(32)
        return session['csrf_token']

    def valid_csrf():
        candidate = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
        expected = session.get('csrf_token', '')
        return bool(expected and isinstance(candidate, str) and hmac.compare_digest(expected.encode('utf-8'), candidate.encode('utf-8')))

    def authenticated():
        with lock:
            now = clock()
            for sid in list(sessions):
                if sessions[sid] <= now:
                    del sessions[sid]
            return configured and session.get('sid') in sessions

    def denied(code, message):
        return jsonify(status='error', msg=message), code

    @app.before_request
    def authorize():
        # Explicit public endpoints. Any newly added endpoint is privileged by default.
        public = {'homepage', 'index', 'live', 'api_articles', 'api_events','api_event_detail',
                  'api_subscription_status', 'api_get_preferences', 'stream_proxy',
                  'serve_edition', 'serve_page', 'admin_login', 'web_asset'}
        if request.endpoint is None:
            return None
        if (request.endpoint not in public or request.endpoint == 'admin_login') and (
                app.config['SESSION_COOKIE_SECURE'] and not request.is_secure):
            return denied(403, 'HTTPS requis pour l’administration')
        if request.endpoint not in public:
            if not authenticated():
                if (request.endpoint in {'config_page', 'config_simple_page', 'config_yaml_page', 'feeds_page', 'preferences_page'}
                        and request.method in ('GET', 'HEAD') and request.accept_mimetypes.best == 'text/html'):
                    return redirect('/admin/login')
                return denied(401, 'Authentification administrateur requise')
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and not valid_csrf():
                return denied(403, 'Jeton CSRF absent ou invalide')
        # Only an authenticated/CSRF-checked import may read a larger JSON body.
        # Other endpoints retain the existing 64 KiB ceiling.
        if request.endpoint == 'sources_import':
            request.max_content_length = 2 * 1024 * 1024
        if request.method == 'POST' and request.endpoint in costly:
            with lock:
                attempts = costly[request.endpoint]
                while attempts and attempts[0] <= clock() - 60:
                    attempts.popleft()
                if len(attempts) >= 5:
                    return denied(429, 'Limite des opérations atteinte')
                attempts.append(clock())
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.endpoint != 'admin_login':
            if request.is_json:
                body = request.get_json(silent=True)
                if not isinstance(body, dict):
                    return denied(400, 'Objet JSON requis')
                # Bound shared route inputs before legacy code uses .strip()/reflection.
                from modules.web.config_security import credential_url
                for key in ('category', 'url', 'direction'):
                    if key in body and (not isinstance(body[key], str) or len(body[key]) > 2048
                                        or any(c in body[key] for c in '<>\r\n\x00')):
                        return denied(400, 'Entrée invalide')
                if 'url' in body and (credential_url(body['url']) or not body['url'].startswith(('http://', 'https://'))):
                    return denied(400, 'URL sans credentials requise')
        return None

    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        if not configured:
            return render_template_string(LOGIN, csrf='', message='Accès administrateur non configuré.'), 503
        if request.method == 'GET':
            return render_template_string(LOGIN, csrf=csrf(), message='Connexion administrateur')
        if not valid_csrf():
            return denied(403, 'Jeton CSRF absent ou invalide')
        # Global limit also applies across spoofed IPs/usernames; one web process only.
        with lock:
            while failures and failures[0] <= clock() - 300:
                failures.popleft()
            if len(failures) >= 10:
                response, code = denied(429, 'Trop de tentatives ; réessayez dans cinq minutes')
                response.headers['Retry-After'] = '300'
                return response, code
            password = request.form.get('password', '')
            user = request.form.get('username', '')
            try:
                valid_password = len(password) <= 1024 and check_password_hash(password_hash, password)
            except (ValueError, TypeError):
                valid_password = False
            if not (hmac.compare_digest(user.encode('utf-8'), username.encode('utf-8')) and valid_password):
                failures.append(clock())
                return render_template_string(LOGIN, csrf=csrf(), message='Identifiants invalides.'), 401
            old_sid = session.get('sid')
            sessions.pop(old_sid, None)
            # Bounded registry: a single administrator should need very few sessions.
            authenticated()
            if len(sessions) >= 32:
                sessions.pop(next(iter(sessions)))
            session.clear()
            session['sid'] = secrets.token_urlsafe(32)
            session.permanent = True
            sessions[session['sid']] = clock() + 3600
            csrf()
        return redirect('/config/yaml', code=303)

    @app.post('/admin/logout')
    def admin_logout():
        with lock:
            sessions.pop(session.get('sid'), None)
        session.clear()
        return redirect('/admin/login', code=303)

    @app.get('/admin/csrf')
    def admin_csrf():
        return jsonify(csrf_token=csrf())

    @app.errorhandler(Exception)
    def safe_error(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return denied(error.code, error.name)
        # Exception messages can contain credentials from filesystem/HTTP/YAML.
        # Do not reflect or log their raw contents.
        return denied(500, 'Erreur interne')

    @app.after_request
    def protect_response(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.path.startswith(('/admin', '/config', '/preferences', '/api/flash', '/audio/flash_')) or authenticated():
            response.headers['Cache-Control'] = 'no-store'
            response.vary.add('Cookie')
        if response.mimetype == 'text/html' and authenticated() and request.path.startswith(('/config', '/preferences')):
            token = csrf()  # URL-safe server-generated token only.
            html = response.get_data(as_text=True)
            injection = '<meta name="csrf-token" content="' + token + '">' + BROWSER_CSRF
            html = html.replace('</head>', injection + '</head>', 1)
            logout = ('<form method="post" action="/admin/logout" style="padding:12px">'
                      '<input type="hidden" name="csrf_token" value="' + token + '">'
                      '<button>Déconnexion admin</button></form>')
            response.set_data(html.replace('</body>', logout + '</body>', 1))
        return response
