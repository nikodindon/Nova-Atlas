from pathlib import Path
from threading import Event

import pytest
from mutagen.id3 import ID3, TCON

from modules.radio.music_library import MusicLibrary, open_track
from modules.web.atlas_web import create_app
from tests.test_admin_security import app, password_hash


def write_mp3(path, genre=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fixture bytes for seeking')
    if genre:
        tags = ID3()
        tags.add(TCON(encoding=3, text=[genre]))
        tags.save(path)
    return path


def test_refresh_learns_genres_without_rereading_unchanged_files(tmp_path):
    root = tmp_path/'music'
    rock = write_mp3(root/'Nested'/'Deep'/'one.MP3', 'Rock')
    unknown = write_mp3(root/'two.mp3')
    library = MusicLibrary(tmp_path, root)
    assert library.scan(Event()) == 2
    assert set(library.genres()) == {'Rock', 'Non renseigné'}
    track = library.choose('rock', '')
    assert library.choose('Rock', track) is None
    assert library.track(track)['path'] == 'Nested/Deep/one.MP3'

    unknown.unlink()
    write_mp3(root/'three.mp3', 'Jazz')
    read = []
    assert library.scan(Event(), reader=lambda file: read.append(file.read(3)) or ['Jazz']) == 2
    assert len(read) == 1
    assert set(library.genres()) == {'Rock', 'Jazz'}
    rock.unlink()
    stop = Event()
    stop.set()
    assert library.scan(stop) is None
    assert library.track(track) is not None


@pytest.mark.parametrize('relative', ['../private.mp3', '//etc/private.mp3', '/absolute.mp3'])
def test_track_reader_rejects_outside_paths(tmp_path, relative):
    with pytest.raises(ValueError, match='invalid_music_path'), open_track(tmp_path, relative):
        pass


def test_personal_track_range_and_stale_replacement(tmp_path):
    root = tmp_path/'music'
    track = write_mp3(root/'one.mp3', 'Rock')
    (root/'link.mp3').symlink_to(track)
    (tmp_path/'data').mkdir()
    library = MusicLibrary(tmp_path/'data', root)
    assert library.scan(Event()) == 1
    app = create_app({'radio': {'personal_music': True}}, root=tmp_path)
    client = app.test_client()
    origin = 'https://localhost'
    base = '/api/radio/personal'
    assert client.get(base+'/genres', base_url=origin).json['genres'] == ['Rock']
    identity = client.get(base+'/next?genre=rock', base_url=origin).json['track']
    url = base+'/track/'+identity+'.mp3'
    result = client.get(url, headers={'Range': 'bytes=0-2'}, base_url=origin)
    assert result.status_code == 206 and result.data == b'ID3'
    assert result.headers['Cache-Control'] == 'no-store'
    track.unlink()
    track.symlink_to(Path(__file__))
    assert client.get(url, base_url=origin).status_code == 404
    assert client.get(base+'/next?genre=a&genre=b', base_url=origin).status_code == 400
    assert client.get(base+'/bulletin', base_url=origin).json == {'bulletin': None}
    assert client.get(base+'/bulletin/unrelated.mp3', base_url=origin).status_code == 404


def test_personal_bulletin_only_serves_current_verified_publication(tmp_path):
    from modules.radio.audio_integrity import AudioInfo, publish_audio
    from modules.radio.personal_bulletin import record_playback

    data = tmp_path/'data'
    data.mkdir()
    queue = tmp_path/'audio_queue'
    cache = queue/'android_cache'
    cache.mkdir(parents=True)
    name = 'bulletin_20261128_093000_' + 'd'*32 + '.mp3'
    staged = write_mp3(cache/'synthetic.mp3')
    target = cache/name
    publish_audio(staged, target, AudioInfo(duration=1.0), 1)
    record_playback(data, queue, name)
    app = create_app({'radio': {'personal_music': True}}, root=tmp_path)
    client = app.test_client()
    base = 'https://localhost'
    state = client.get('/api/radio/personal/bulletin', base_url=base)
    assert state.json['bulletin']['name'] == name
    audio = client.get('/api/radio/personal/bulletin/'+name, base_url=base)
    assert audio.status_code == 200 and audio.mimetype == 'audio/mpeg'
    target.write_bytes(b'changed')
    assert client.get('/api/radio/personal/bulletin/'+name, base_url=base).status_code == 404


def test_personal_track_catalogue_can_be_limited_to_lan_origins(tmp_path):
    app = create_app({'radio': {'personal_music': True, 'personal_music_hosts': [
        '192.168.1.32:15173', 'localhost:15173',
    ]}}, root=tmp_path)
    client = app.test_client()
    path = '/api/radio/personal/genres'
    assert client.get(path, base_url='https://public.example').status_code == 404
    assert client.get(path, base_url='http://192.168.1.32:15173').status_code == 200


def test_external_personal_music_requires_admin_session(app):
    from tests.test_admin_security import get, login

    paths = app.extensions['atlas_paths']
    config = {'radio': {
        'personal_music': True,
        'personal_music_hosts': ['lan.example'],
        'personal_music_admin_hosts': ['localhost'],
    }}
    enabled = create_app(config, root=paths['root'])
    client = enabled.test_client()
    path = '/api/radio/personal/genres'
    assert get(client, path).status_code == 401
    assert client.get(path, base_url='https://lan.example').status_code == 200
    login(client)
    assert get(client, path).status_code == 200
