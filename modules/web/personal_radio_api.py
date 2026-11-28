"""Same-origin, opt-in music catalogue and ranged MP3 delivery."""
import os
import re
import sqlite3

from flask import Response, abort, jsonify, request, send_file
from werkzeug.wsgi import wrap_file

from modules.radio.audio_integrity import validated_publication
from modules.radio.music_library import MusicLibrary, open_track
from modules.radio.personal_bulletin import most_recent

TRACK_ID = re.compile(r'[a-f0-9]{64}')


def install_personal_radio(app, config, paths):
    radio = config.get('radio', {})
    if radio.get('personal_music') is not True:
        return
    hosts = radio.get('personal_music_hosts')
    if hosts is not None and (not isinstance(hosts, list) or not hosts or
            any(not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9.:-]{1,255}', host) for host in hosts)):
        raise ValueError('invalid_personal_music_hosts')
    allowed_hosts = {host.lower() for host in hosts} if hosts else None

    @app.before_request
    def personal_radio_origin():
        if (allowed_hosts is not None and request.path.startswith('/api/radio/personal/')
                and request.host.lower() not in allowed_hosts):
            abort(404)

    settings = config.get('paths', {})
    root = paths['root']
    queue = root / settings.get('audio_queue', 'audio_queue')
    library = MusicLibrary(paths['data'], root / settings.get('music', 'music'))

    @app.after_request
    def personal_radio_headers(response):
        if request.path.startswith('/api/radio/personal/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/radio/personal/genres')
    def personal_genres():
        if request.args:
            abort(400)
        try:
            genres = library.genres()
        except (sqlite3.Error, ValueError):
            return jsonify(genres=[], available=False)
        return jsonify(genres=genres, available=True)

    @app.get('/api/radio/personal/next')
    def personal_next():
        if set(request.args) - {'genre', 'previous'} or any(len(request.args.getlist(key)) != 1 for key in request.args):
            abort(400)
        genre, previous = request.args.get('genre', ''), request.args.get('previous', '')
        if len(genre) > 80 or (previous and not TRACK_ID.fullmatch(previous)):
            abort(400)
        try:
            identity = library.choose(genre, previous)
        except (sqlite3.Error, ValueError):
            abort(503)
        return jsonify(track=identity)

    @app.get('/api/radio/personal/track/<identity>.mp3')
    def personal_track(identity):
        if not TRACK_ID.fullmatch(identity):
            abort(404)
        try:
            row = library.track(identity)
            if row is None:
                abort(404)
            with open_track(library.root, row['path']) as (track, info):
                if (info.st_mtime_ns, info.st_size) != (row['mtime'], row['size']):
                    abort(404)
                stream = os.fdopen(os.dup(track.fileno()), 'rb')
        except (OSError, sqlite3.Error, ValueError):
            abort(404)
        try:
            response = Response(wrap_file(request.environ, stream), mimetype='audio/mpeg', direct_passthrough=True)
            response.content_length = info.st_size
            response.set_etag(identity + '-' + str(info.st_mtime_ns))
            return response.make_conditional(request, accept_ranges=True, complete_length=info.st_size)
        except Exception:
            stream.close()
            raise

    @app.get('/api/radio/personal/bulletin')
    def personal_bulletin_state():
        try:
            return jsonify(bulletin=most_recent(paths['data']))
        except (OSError, ValueError):
            return jsonify(bulletin=None)

    @app.get('/api/radio/personal/bulletin/<name>')
    def personal_bulletin_audio(name):
        try:
            current = most_recent(paths['data'])
            if current is None or current['name'] != name:
                abort(404)
            source = queue / 'android_cache' / name
            if not validated_publication(source):
                abort(404)
            return send_file(source, mimetype='audio/mpeg', conditional=True)
        except (OSError, ValueError):
            abort(404)
