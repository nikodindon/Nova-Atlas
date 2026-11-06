"""Synthetic editorial evidence; no network, model, TTS or user files."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timezone,timedelta
import hashlib
import json
import time
import pytest
from modules.editorial.events import Coverage,compare,group_coverages,VerificationAdvisor
from modules.editorial.index import EventIndex,IndexError,override_state
from modules.editorial.selection import select,opt_in,prompt_provenance

NOW=datetime(2026,11,6,12,tzinfo=timezone.utc)
TITLE='Le ministre Martin annonce sa démission du gouvernement français'
SUMMARY='Le ministre Martin annonce sa démission du gouvernement français après le vote parlementaire. Les élus commentent cette démission.'


def article(i=0,title=TITLE,summary=SUMMARY,when=None,**kw):
    url=f'https://media{i}.invalid/article'
    return {'hash':hashlib.md5(url.encode()).hexdigest(),'title':title,'title_original':title,'summary':summary,'link':url,'source':f'Média {i}','category':'france','pub_date':(when or NOW).isoformat(),'timestamp':(when or NOW).replace(tzinfo=None).isoformat(),**kw}


def cover(i=0,**kw):return Coverage.from_article(article(i,**kw))


def test_same_event_distinct_media_preserved():
    values=[cover(0),cover(1)];events,uncertain=group_coverages(values,now=NOW.isoformat())
    assert len(events)==1 and events[0]['article_count']==2 and events[0]['domain_count']==2
    assert events[0]['independent_confirmation'] is False and events[0]['event_at'] is None
    assert {a['link'] for a in events[0]['articles']}=={c.link for c in values}
    assert 'shared_action' in events[0]['reasons']


@pytest.mark.parametrize('other',[
 'Le ministre Martin annonce sa nomination au conseil européen',
 'Donald Trump annonce une hausse des taxes douanières',
 'La ministre Martin annonce une attaque du gouvernement français',
 'Le ministre Martin annonce sa démission du gouvernement italien',
])
def test_topic_and_person_are_not_identity(other):
    assert compare(cover(0),cover(1,title=other)).status!='group'


@pytest.mark.parametrize('left,right',[
 ('La France annonce une élection présidentielle le 12 avril','La France annonce une élection législative le 18 juin'),
 ('Une attaque à Paris lundi provoque une enquête judiciaire','Une attaque à Paris vendredi provoque une enquête judiciaire'),
 ('La BCE annonce une baisse des taux à 3 pour cent','La BCE annonce une baisse des taux à 2 pour cent'),
 ('Le Bitcoin annonce une hausse à 80000 dollars sur les marchés','Le Bitcoin annonce une hausse à 81000 dollars sur les marchés'),
 ('Un accident de train à Lyon lundi provoque une enquête','Un accident de train à Lyon jeudi provoque une enquête'),
 ('Le ministre Martin annonce sa démission du gouvernement français','Le ministre Martin dément sa démission du gouvernement français'),
])
def test_hard_negatives(left,right):
    a,b=cover(0,title=left),cover(1,title=right)
    assert compare(a,b).status!='group'
    assert len(group_coverages([a,b])[0])==2


def test_aware_date_window_and_ambiguous_dates():
    assert compare(cover(0),cover(1,when=NOW+timedelta(days=3))).status!='group'
    a=cover(0,pub_date='2026-11-06T12:00:00');b=cover(1,pub_date='2026-11-06T12:00:00')
    assert a.published_at is None and a.publication_status=='ambiguous_local'
    assert compare(a,b).status=='uncertain'


def test_existing_translation_used_no_new_translation():
    translated=cover(1,title_original='Le ministre japonais...')
    translated=Coverage.from_article(article(1,title_original='Minister Martin resigns from the French government',language='en'))
    events,_=group_coverages([cover(0),translated]);assert len(events)==1
    assert events[0]['articles'][0]['original_title'] in (TITLE,'Minister Martin resigns from the French government')
    original=cover(2,title='Minister Martin resigns from the French government',summary='English summary')
    assert compare(cover(0),original).status!='group'


def test_missing_summary_title_or_date_conservative():
    assert len(group_coverages([cover(0,summary=''),cover(1,summary='')])[0])==2 # no supporting summary evidence
    assert len(group_coverages([cover(0,pub_date=''),cover(1,pub_date='')])[0])==2
    with pytest.raises(ValueError):Coverage.from_article({'title':''})


def test_denial_is_flagged_as_possible_not_judged_true():
    events,uncertain=group_coverages([cover(0),cover(1,title=TITLE.replace('annonce','dément'))])
    assert len(events)==2 and any('possible_denial_or_disagreement' in e['disagreements'] for e in events)
    assert all(e['independent_confirmation'] is False for e in events)


def test_complete_link_no_transitive_chain():
    a=cover(0,title='Martin annonce sa démission du gouvernement français mardi')
    b=cover(1,title='Martin annonce sa démission du gouvernement français')
    c=cover(2,title='Martin annonce sa démission du gouvernement français jeudi')
    groups,_=group_coverages([a,b,c])
    assert not any({a.id,c.id}<={row['id'] for row in group['articles']} for group in groups)


def test_order_independence_and_restart_ids():
    items=[cover(i) for i in range(5)]
    first,_=group_coverages(items,now=NOW.isoformat());reverse,_=group_coverages(items[::-1],now=NOW.isoformat())
    assert first==reverse
    rebuilt,_=group_coverages(items,previous=first,now=(NOW+timedelta(hours=1)).isoformat())
    assert rebuilt==first
    expanded,_=group_coverages(items+[cover(8)],previous=first,now=(NOW+timedelta(hours=1)).isoformat())
    assert expanded[0]['event_id']==first[0]['event_id'] and expanded[0]['article_count']==6


def test_multiple_occurrences_same_url_categories_not_erased():
    a=article(0);b={**a,'category':'europe'}
    values=[Coverage.from_article(a),Coverage.from_article(b)]
    assert len({v.id for v in values})==2
    groups,_=group_coverages(values);assert sum(g['article_count'] for g in groups)==2


def test_single_source_duplicate_wires_not_independent_confirmation():
    values=[cover(i,link='https://same.invalid/'+str(i),source='Même agence') for i in range(4)]
    groups,_=group_coverages(values);assert groups[0]['article_count']==4 and groups[0]['domain_count']==1
    assert groups[0]['independent_confirmation'] is False


def test_human_split_merge_title_and_conflicts():
    a,b=cover(0),cover(1)
    split={'separate':[[a.id,b.id]],'merge':[],'titles':{a.id:'Titre choisi'}}
    events,_=group_coverages([a,b],overrides=split);assert len(events)==2 and any(e['title']=='Titre choisi' for e in events)
    merged,_=group_coverages([a,b],overrides={'merge':[[a.id,b.id]]});assert merged[0]['status']=='human_association'
    with pytest.raises(ValueError):group_coverages([a,b],overrides={**split,'merge':[[a.id,b.id]]})


@pytest.fixture
def index(tmp_path):
    articles=tmp_path/'articles';articles.mkdir();data=tmp_path/'data';data.mkdir()
    (articles/'20261106_articles.json').write_text(json.dumps([article(0),article(1)]))
    return EventIndex(articles,data,clock=lambda:NOW)


def test_index_dry_run_no_side_effect_and_atomic_idempotent(index):
    originals={p.name:p.read_bytes() for p in index.articles.iterdir()}
    result=index.rebuild();assert result['status']=='ready' and not list(index.data.iterdir())
    index.rebuild(apply=True,expected=result['input_sha']);old=index.path.read_bytes()
    index.rebuild(apply=True,expected=result['input_sha']);assert index.path.read_bytes()==old
    assert {p.name:p.read_bytes() for p in index.articles.iterdir()}==originals
    assert index.path.stat().st_mode&0o777==0o600


def test_rebuild_crash_keeps_previous_index(index,monkeypatch):
    from modules.core import source_manager
    first=index.rebuild();index.rebuild(apply=True,expected=first['input_sha']);before=index.path.read_bytes()
    real=source_manager.os.replace
    def fail(src,dst):
        if dst==index.path:raise OSError('synthetic interruption')
        return real(src,dst)
    monkeypatch.setattr(source_manager.os,'replace',fail)
    with pytest.raises(OSError):index.rebuild(apply=True,expected=first['input_sha'])
    assert index.path.read_bytes()==before and not list(index.data.glob('*.tmp'))


def test_corrupt_index_preserved_and_never_overwritten(index):
    index.path.write_text('{broken')
    with pytest.raises(IndexError):index.rebuild(apply=True,expected='x')
    assert index.path.read_text()=='{broken'


def test_invalid_documents_reported_without_article_deletion(index):
    bad=index.articles/'20261105_articles.json';bad.write_text('broken')
    result=index.rebuild();assert result['status']=='degraded' and result['diagnostics'][0]['state']=='invalid_json'
    assert bad.read_text()=='broken'
    with pytest.raises(IndexError):index.rebuild(apply=True,expected='unreviewed')


def test_overrides_survive_rebuild_and_require_new_index(index):
    result=index.rebuild();index.rebuild(apply=True,expected=result['input_sha']);a,b=[row['id'] for row in result['events'][0]['articles']]
    preview=index.update_overrides({'separate':[[a,b]],'titles':{a:'Titre opérateur'}})
    assert not index.overrides.exists()
    index.update_overrides({'separate':[[a,b]],'titles':{a:'Titre opérateur'}},apply=True,expected=preview['current_sha'],reason='mauvaise association')
    before=index.overrides.read_bytes()
    with pytest.raises(IndexError,match='overrides_require_rebuild'):index.load()
    rebuilt=index.rebuild(apply=True,expected=result['input_sha']);assert len(rebuilt['events'])==2
    assert index.overrides.read_bytes()==before and any(e['title']=='Titre opérateur' for e in rebuilt['events'])


def test_override_version_and_reason_required(index):
    with pytest.raises(IndexError):index.update_overrides({},apply=True,expected='bad')
    assert not index.overrides.exists()


def test_symlinks_and_retention(index,tmp_path):
    outside=tmp_path/'outside';outside.write_text('private')
    (index.articles/'20261105_articles.json').symlink_to(outside)
    result=index.rebuild();assert result['status']=='degraded' and outside.read_text()=='private'
    old=index.articles/'20200101_articles.json';old.write_text('old history')
    result=index.rebuild();assert old.name not in result['manifest'] and old.read_text()=='old history'


def test_selector_opt_in_provenance_score_and_no_mutation():
    articles=[article(0),article(1),article(2,title='La BCE annonce une hausse des taux directeurs européens',category='economie')];before=deepcopy(articles)
    assert opt_in(articles,{}) is articles
    packets=select(articles,weights={'economie':4},now=NOW)
    assert len(packets)==2 and packets[0]['category']=='economie'
    assert sum(len(p['_editorial_event']['coverages']) for p in packets)==3
    assert 'confirmation indépendante non établie' in prompt_provenance(packets[0])
    assert articles==before


def test_bounded_group_and_large_volume():
    started=time.monotonic();events,_=group_coverages([cover(i) for i in range(75)])
    assert all(e['article_count']<=50 for e in events) and sum(e['article_count'] for e in events)==75
    assert time.monotonic()-started<10
    with pytest.raises(ValueError):group_coverages([cover(i) for i in range(501)])


def test_advisor_contract_disabled_and_bounded():
    advisor=VerificationAdvisor();assert advisor.review({'left':'a','right':'b'})['status']=='disabled'
    with pytest.raises(ValueError):advisor.review({'text':'x'*20000})
    advisor.enabled=True
    with pytest.raises(NotImplementedError):advisor.review({})


def _rebuild_process(articles,data,fingerprint,queue):
    try:
        result=EventIndex(articles,data,clock=lambda:NOW).rebuild(apply=True,expected=fingerprint)
        queue.put(result['events'][0]['event_id'])
    except Exception as exc:queue.put(type(exc).__name__)


def test_two_rebuilders_share_stable_lock(index):
    import multiprocessing
    context=multiprocessing.get_context('fork');queue=context.Queue();snapshot=index.rebuild()
    processes=[context.Process(target=_rebuild_process,args=(index.articles,index.data,snapshot['input_sha'],queue)) for _ in range(2)]
    for process in processes:process.start()
    for process in processes:process.join(5);assert process.exitcode==0
    assert queue.get(timeout=1)==queue.get(timeout=1)==snapshot['events'][0]['event_id']
    assert (index.data/'editorial-events.lock').exists()


def test_apply_rejects_changed_article_snapshot(index):
    first=index.rebuild();path=index.articles/'20261106_articles.json';path.write_text(json.dumps([article(0)]))
    with pytest.raises(IndexError,match='input_changed'):index.rebuild(apply=True,expected=first['input_sha'])
    assert not index.path.exists()


def test_midnight_paris_and_leap_day_retention(tmp_path):
    articles=tmp_path/'articles';articles.mkdir();data=tmp_path/'data';data.mkdir()
    clock=lambda:datetime(2028,2,29,23,30,tzinfo=timezone.utc)
    for day in ('20280229','20280301'):(articles/f'{day}_articles.json').write_text(json.dumps([article(0)]))
    result=EventIndex(articles,data,clock=clock,days=2).rebuild()
    assert set(result['manifest'])=={'20280229_articles.json','20280301_articles.json'}


@pytest.mark.parametrize('mutation',[lambda d:d.pop('status'),lambda d:d.pop('generated_at'),lambda d:d.update(version=2),lambda d:d.update(algorithm='unknown'),lambda d:d['events'][0].update(independent_confirmation=True),lambda d:d['events'][0].update(latest_published_at='2026-11-06T12:00:00'),lambda d:d['events'][0].pop('title')])
def test_semantically_corrupt_index_is_preserved(index,mutation):
    result=index.rebuild();index.rebuild(apply=True,expected=result['input_sha']);raw=json.loads(index.path.read_text());mutation(raw);index.path.write_text(json.dumps(raw));before=index.path.read_bytes()
    with pytest.raises(IndexError):index.rebuild(apply=True,expected=result['input_sha'])
    assert index.path.read_bytes()==before


def test_numbers_in_summary_prevent_generic_title_merge():
    a=cover(0,summary=SUMMARY+' Le bilan est de 10 personnes.')
    b=cover(1,summary=SUMMARY+' Le bilan est de 20 personnes.')
    assert compare(a,b).status!='group' and 'different_summary_numbers' in compare(a,b).reasons


def test_transitive_manual_conflict_is_refused_before_save(index):
    a,b,c=[cover(i).id for i in range(3)]
    patch={'merge':[[a,b],[b,c]],'separate':[[a,c]]}
    with pytest.raises(IndexError):index.update_overrides(patch,apply=True,expected=override_state(index.overrides)[1],reason='test conflict')
    assert not index.overrides.exists()


def test_orphaned_human_rules_are_preserved_and_signalled(index):
    identifier='a'*64;preview=index.update_overrides({'titles':{identifier:'Ancien titre'}})
    index.update_overrides({'titles':{identifier:'Ancien titre'}},apply=True,expected=preview['current_sha'],reason='prior review')
    before=index.overrides.read_bytes();result=index.rebuild()
    assert result['status']=='degraded' and any(d['state']=='override_references_outside_snapshot' for d in result['diagnostics'])
    assert index.overrides.read_bytes()==before
