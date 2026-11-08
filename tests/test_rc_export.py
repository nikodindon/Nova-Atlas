"""Read-only image root reproduction; synthetic paths only."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from modules.media import export as module
from modules.media.store import MediaError


def test_export_staging_is_inside_writable_site_mount(tmp_path,monkeypatch):
    site=tmp_path/'site';site.mkdir();seen=[];original=module.tempfile.mkdtemp
    def temporary(*args,**kwargs):
        seen.append(Path(kwargs['dir']))
        assert Path(kwargs['dir'])==site/'media'
        return original(*args,**kwargs)
    monkeypatch.setattr(module.tempfile,'mkdtemp',temporary)
    module.export_images(SimpleNamespace(store=SimpleNamespace(mode='disabled')),site)
    assert (site/'media/images').is_dir() and seen
    assert not list((site/'media').glob('.media-export-*'))


def test_failed_tree_exchange_preserves_previous_export(tmp_path,monkeypatch):
    site=tmp_path/'site';old=site/'media/images';old.mkdir(parents=True);(old/'previous').write_bytes(b'approved synthetic')
    monkeypatch.setattr(module.ctypes,'CDLL',lambda *a,**k:SimpleNamespace(renameat2=Mock(return_value=-1)))
    with pytest.raises(MediaError):module.export_images(SimpleNamespace(store=SimpleNamespace(mode='disabled')),site)
    assert (old/'previous').read_bytes()==b'approved synthetic'
    assert not list(site.rglob('.media-export-*'))
