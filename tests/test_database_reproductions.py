from pathlib import Path
import yaml


def test_non_destructive_snapshot_planner_exists():
    from modules.persistence.legacy import plan_import
    assert callable(plan_import)


def test_postgres_is_an_optional_internal_service():
    path=Path(__file__).resolve().parents[1]/'docker-compose.database.yml'
    config=yaml.safe_load(path.read_text())
    assert 'ports' not in config['services']['postgres']


def test_alembic_has_initial_revision():
    assert list((Path(__file__).resolve().parents[1]/'migrations/versions').glob('*.py'))
