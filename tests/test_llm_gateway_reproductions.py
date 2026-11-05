"""Missing routing/capacity contracts reproduced before Sprint 03 changes."""
from pathlib import Path
import yaml


def test_gateway_backend_has_explicit_alias_adapter():
    from modules.core.llm_gateway import alias_for
    assert alias_for('fetch','translate')=='atlas-translate'


def test_compose_gateway_is_optional_and_unpublished():
    config=yaml.safe_load((Path(__file__).resolve().parents[1]/'docker-compose.gateway.yml').read_text())
    assert 'ports' not in config['services']['litellm']


def test_gateway_cache_separates_effective_models():
    from modules.core.llm_gateway import cache_identity
    assert cache_identity('prompt','fr','atlas-fast','one','v1','summary') != cache_identity('prompt','fr','atlas-fast','two','v1','summary')
