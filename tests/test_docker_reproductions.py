"""Baseline deployment failures, checked without a Docker daemon or services."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]


def test_separate_service_modes_exist():
    services=yaml.safe_load((ROOT/'docker-compose.yml').read_text())['services']
    assert {'web','news','radio','icecast'} <= set(services)


def test_no_mutable_latest_icecast_image():
    image=yaml.safe_load((ROOT/'docker-compose.yml').read_text())['services']['icecast']['image']
    assert ':latest' not in image
    assert '@sha256:' in image or '@sha256:' in (ROOT/'deploy/Dockerfile.icecast').read_text().splitlines()[0]


def test_no_operational_default_passwords():
    config=yaml.safe_load((ROOT/'docker-compose.yml').read_text())
    assert 'hackme' not in str(config)


def test_no_wildcard_host_port():
    ports=yaml.safe_load((ROOT/'docker-compose.yml').read_text())['services']['icecast'].get('ports',[])
    assert all(str(value).startswith('127.0.0.1:') or (isinstance(value,dict) and value.get('host_ip')=='127.0.0.1') for value in ports)
