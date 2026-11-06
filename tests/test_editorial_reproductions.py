import json
from datetime import datetime
from modules.web.atlas_web import get_breaking_news
from modules.radio.bulletin_generator import dedup_articles_by_similarity


def test_two_common_words_are_not_an_event(tmp_path):
    now=datetime.now();path=tmp_path/f'{now:%Y%m%d}_articles.json'
    path.write_text(json.dumps([{'title':title,'summary':'texte','link':f'https://source{i}.invalid/a','timestamp':now.isoformat()} for i,title in enumerate(['Donald Trump annonce une nomination diplomatique','Donald Trump conteste les résultats du scrutin'])]))
    assert get_breaking_news({'articles':tmp_path})==[]


def test_shared_summary_prefix_cannot_remove_different_facts():
    prefix='Dans le contexte international actuel, les observateurs analysent la situation. '*4
    articles=[{'title':title,'summary':prefix+ending,'link':f'https://site{i}.invalid/a'} for i,(title,ending) in enumerate([('Attaque à Paris lundi','un fait distinct lundi'),('Attaque à Paris vendredi','un autre fait vendredi')])]
    assert len(dedup_articles_by_similarity(articles))==2
