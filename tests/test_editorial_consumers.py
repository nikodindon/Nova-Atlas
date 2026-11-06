from copy import deepcopy
import json
from pathlib import Path
import pytest
from modules.editorial.categories import registry,ordered_categories
from modules.editorial.index import EventIndex
from modules.editorial.selection import opt_in,prompt_provenance
from modules.editorial.quality import measure
from modules.editions.atlas_editions import EditionGenerator
from modules.radio.bulletin_generator import _format_articles_for_prompt
from modules.report import atlas_report
from tests.test_editorial_events import article,NOW


def test_registry_configured_and_unknown_categories_preserved(tmp_path):
    path=tmp_path/'feeds.yaml';path.write_text('personnalisee:\n - https://public.invalid/rss\nvide:\n')
    values=registry(path,[{'category':'ancienne_inconnue'}]);mapping={c.id:c for c in values}
    assert values[0].id=='personnalisee' and values[1].id=='vide'
    assert mapping['personnalisee'].active and not mapping['vide'].active
    assert mapping['ancienne_inconnue'].active and mapping['ancienne_inconnue'].icon=='🌐'
    assert ordered_categories([{'category':'inconnue'}],path)==['inconnue']


def test_editions_custom_categories_in_context_and_legacy_unchanged(tmp_path):
    generator=EditionGenerator({'paths':{'articles_dir':str(tmp_path/'articles'),'editions_dir':str(tmp_path/'editions')}})
    custom=article(category='personnalisee');original=deepcopy(custom)
    prompt=generator._build_prompt('midi',[custom],'20261106')
    assert 'PERSONNALISEE' in prompt and custom==original and '_editorial_event' not in prompt


def test_opt_in_edition_provenance_and_private_manifest(tmp_path):
    config={'paths':{'articles_dir':str(tmp_path/'articles'),'editions_dir':str(tmp_path/'editions'),'data_dir':str(tmp_path)},'editorial_events':{'enabled':True}}
    generator=EditionGenerator(config);rows=[article(0),article(1)];before=deepcopy(rows)
    prompt=generator._build_prompt('midi',rows,'20261106')
    assert 'confirmation indépendante non établie' in prompt and 'https://media0.invalid/article' in prompt and 'https://media1.invalid/article' in prompt
    manifest=json.loads((tmp_path/'editorial_selection_editions.json').read_text())
    assert len(manifest['packets'])==1 and len(manifest['packets'][0]['coverages'])==2 and rows==before


def test_radio_format_provenance_opt_in_only():
    from modules.radio.bulletin_generator import _format_articles_for_prompt
    rows=[article(0),article(1)];plain=_format_articles_for_prompt(rows)
    assert 'confirmation indépendante' not in plain
    packed=opt_in(rows,{'editorial_events':{'enabled':True}})
    prompt=_format_articles_for_prompt(packed)
    assert prompt.count('confirmation indépendante non établie')==1
    assert 'https://media0.invalid/article' in prompt and 'https://media1.invalid/article' in prompt


def test_small_annotated_corpus_quality_scope_explicit():
    path=Path(__file__).parent/'fixtures/editorial/pairs.json';report=measure(json.loads(path.read_text()))
    assert report['pairs']==24 and report['algorithms']['conservative_v1']['false_merge']==0
    assert report['algorithms']['conservative_v1']['missed_group']==2
    assert report['algorithms']['conservative_v1']['ambiguous_proposed']==0
    assert report['algorithms']['historical_two_words']['false_merge']>0


def test_reports_custom_category_not_dropped(tmp_path,monkeypatch):
    from modules.report import atlas_report as report
    monkeypatch.setattr(report,'init_ollama',lambda config:None)
    monkeypatch.setattr(report,'get_model',lambda:'synthetic-model')
    generator=report.ReportGenerator({'paths':{'articles_dir':str(tmp_path),'reports_dir':str(tmp_path/'reports')}})
    monkeypatch.setattr(generator,'_load_articles',lambda day:[article(category='personnalisee')])
    calls=[]
    monkeypatch.setattr(generator,'_generate_section',lambda cat,*args:(calls.append(cat) or 'Section synthétique'))
    monkeypatch.setattr(generator,'_generate_intro',lambda *args:'Introduction')
    monkeypatch.setattr(generator,'_generate_conclusion',lambda *args:'Conclusion')
    generator.generate('20261106')
    assert calls==['personnalisee']


def test_legacy_edition_order_and_summary_format_without_grouping(tmp_path):
    generator=EditionGenerator({'paths':{'articles_dir':str(tmp_path),'editions_dir':str(tmp_path/'editions')}})
    rows=[article(0,category='economie'),article(1,category='france')]
    prompt=generator._build_prompt('midi',rows,'20261106')
    assert prompt.index('[FRANCE')<prompt.index('[ÉCONOMIE')
    assert 'confirmation indépendante' not in prompt


def test_opt_in_respects_persisted_operator_split(tmp_path):
    index=EventIndex(tmp_path,tmp_path,clock=lambda:NOW)
    rows=[article(0),article(1)]
    from modules.editorial.events import Coverage
    a,b=[Coverage.from_article(r).id for r in rows]
    preview=index.update_overrides({'separate':[[a,b]]})
    index.update_overrides({'separate':[[a,b]]},apply=True,expected=preview['current_sha'],reason='separate facts')
    packets=opt_in(rows,{'paths':{'data_dir':str(tmp_path)},'editorial_events':{'enabled':True}})
    assert len(packets)==2 and all(p['_editorial_event']['overrides_sha'] for p in packets)


@pytest.mark.parametrize('enabled,expected_packets',[(False,2),(True,1)])
def test_radio_opt_in_preserves_validated_publication(tmp_path,monkeypatch,enabled,expected_packets):
    from tests.test_radio_reproductions import pipeline,SCRIPT
    from modules.radio import bulletin_generator as bg
    generator=pipeline.__wrapped__(tmp_path,monkeypatch)
    generator.paths['data']=tmp_path
    generator.config={'paths':{'data_dir':str(tmp_path)},'editorial_events':{'enabled':enabled}}
    captured=[]
    monkeypatch.setattr(bg,'generate_bulletin_script',lambda articles,*args:(captured.extend(articles) or SCRIPT))
    rows=[article(0),article(1)];before=deepcopy(rows)
    path=generator.build(rows)
    assert path is not None and path.exists() and len(captured)==expected_packets and rows==before
    assert (tmp_path/'editorial_selection_radio.json').exists()==enabled


def test_radio_missing_segment_still_refuses_event_bulletin(tmp_path,monkeypatch):
    from tests.test_radio_reproductions import pipeline,SCRIPT
    from modules.radio import bulletin_generator as bg
    generator=pipeline.__wrapped__(tmp_path,monkeypatch);generator.paths['data']=tmp_path
    generator.config={'paths':{'data_dir':str(tmp_path)},'editorial_events':{'enabled':True}}
    monkeypatch.setattr(bg,'generate_bulletin_script',lambda *args:SCRIPT)
    monkeypatch.setattr(bg,'synthesize_segment',lambda *args,**kw:False)
    assert generator.build([article(0),article(1)]) is None
    assert not list(generator.queue_dir.glob('bulletin_*.mp3'))
