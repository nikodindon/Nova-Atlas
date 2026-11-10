"""Synthetic regressions. Altered synthetic fragments illustrate loss; no private historical export is published."""
import json
from unittest.mock import Mock, patch
import pytest
from modules.fetch.atlas_fetch import ArticleFetcher, _clean_summary

@pytest.mark.parametrize('value',[
 'Le lecteur consulte le document.', 'Une note de demande est publiée.',
 'Le total concerne 202 2026 contrats distincts.', 'Tokyo 東京 et 北京 publient un accord.',
 'Le programme conserve le Plan C', 'Une précision (montant de 20 euros',
 'Une hausse très très importante.', 'Les critères de demain sont connus.',
])
def test_cleanup_preserves_valid_or_uncertain_words_and_numbers(value):
    assert _clean_summary(value)==value


def test_cleanup_removes_only_unambiguous_conversational_heading():
    assert _clean_summary("Voici un résumé clair et complet de l'article : Le conseil vote.")=='Le conseil vote.'


def make_fetcher(tmp_path):
    obj=ArticleFetcher.__new__(ArticleFetcher)
    obj._config={'paths':{'data_dir':str(tmp_path),'articles_dir':str(tmp_path/'articles')},
                 'llm':{'provider':'llama-server','model':'fixture-a'}}
    obj._apply_config(obj._config);obj.log=Mock()
    return obj

@pytest.mark.parametrize('change',['tail','language','category','model'])
def test_summary_cache_namespace_separates_generation_inputs(tmp_path,change):
    obj=make_fetcher(tmp_path);content='Une source synthétique indépendante. '*30
    lang=['français']
    with patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=30), patch('modules.fetch.atlas_fetch.get_language',side_effect=lambda:lang[0]), patch('modules.fetch.atlas_fetch.ollama_call',return_value='Le conseil publie un rapport.') as call:
        obj._summarize('Un titre synthétique',content,'science');first=call.call_args.kwargs['cache_key']
        category='science'
        if change=='tail':content=content[:600]+'La date exacte est le 18 juin.'
        if change=='language':lang[0]='english'
        if change=='category':category='tech'
        if change=='model':obj.config['llm']['model']='fixture-b'
        obj._summarize('Un titre synthétique',content,category)
        assert first!=call.call_args.kwargs['cache_key']

from modules.fetch import editorial as ed
from modules.fetch.article_state import ArticleAttemptError


def qualify(obj,**settings):
    cfg=obj.config
    cfg['fetch']={'editorial':{'prompt_version':'editorial-v2',**settings}}
    obj._apply_config(cfg)
    return obj


def test_new_prompt_is_explicit_versioned_soft_length_and_structured(tmp_path):
    obj=qualify(make_fetcher(tmp_path))
    response=json.dumps({'status':'ok','summary':'Le conseil a adopté un budget de 120 euros.'})
    with patch('modules.fetch.atlas_fetch.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=30),patch('modules.fetch.atlas_fetch.ollama_call',return_value=response) as call:
        assert obj._summarize('Budget municipal','Le conseil adopte un budget de 120 euros. '*4,'france').startswith('Le conseil')
    prompt=call.call_args.args[0]
    assert 'summary-editorial-v2' in prompt and 'insufficient_source' in prompt and 'Cible souple' in prompt
    assert obj._summary_evidence['raw_response']==response

@pytest.mark.parametrize('response,reason',[
 ('Voici un résumé : incorrect','summary_invalid_contract'),
 ('{"status":"insufficient_source","summary":""}','summary_source_insufficient'),
 ('{"status":"ok","summary":"Note : le budget est provisoire."}','summary_invalid_contract'),
 ('{"status":"ok","summary":"[Timeout]"}','summary_invalid_contract'),
 ('{"status":"ok","summary":"Voici les faits du jour."}','summary_invalid_contract'),
 ('{"status":"ok","summary":"texte","reasoning_content":"private thought"}','summary_reasoning_or_oversized')])
def test_internal_markers_and_reasoning_never_become_news(tmp_path,response,reason):
    obj=qualify(make_fetcher(tmp_path))
    with patch('modules.fetch.atlas_fetch.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=30),patch('modules.fetch.atlas_fetch.ollama_call',return_value=response):
        with pytest.raises(ArticleAttemptError,match=reason):obj._summarize('Titre synthétique','Une source suffisamment longue et indépendante. '*4,'tech')
    if 'reasoning' in response:assert obj._summary_evidence['raw_response'] is None


def test_insufficient_short_source_does_not_call_model(tmp_path):
    obj=qualify(make_fetcher(tmp_path))
    with patch('modules.fetch.atlas_fetch.ollama_call') as call:
        with pytest.raises(ArticleAttemptError,match='source_insufficient'):obj._summarize('Titre','... !','tech')
    call.assert_not_called()

@pytest.mark.parametrize('value',[
 'épreuves du bocage 2031', 'cet éch municipal', 'trois durante',
 'La politique de la consiste…', 'géistes du calcul', "L'président", 'Norway dément la annonce',
 'François 東京 — 10,5 millions € et Qwen3.6-35B-A3B',
 'La valeur 202\n2026 est conservée, sans deviner une correction.',
 'La mention Note : budget provisoire est importante.',
 'Voici les dates annoncées par la mairie.' ])
def test_uncertain_historical_fragments_are_not_magically_repaired(value):
    assert _clean_summary(value)==value


def test_same_language_title_preserved_without_reformulation(tmp_path):
    obj=qualify(make_fetcher(tmp_path))
    with patch('modules.core.llm_client.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.ollama_call') as call:
        assert obj._translate_title('La commune publie son budget',source_language='fr')=='La commune publie son budget'
    call.assert_not_called()


def test_unknown_language_is_not_guessed_from_foreign_names(tmp_path):
    obj=qualify(make_fetcher(tmp_path))
    with patch('modules.core.llm_client.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.ollama_call') as call:
        assert obj._translate_title('OpenAI et China Mobile à Paris')=='OpenAI et China Mobile à Paris'
    call.assert_not_called()

@pytest.mark.parametrize('output,expected',[
 ('Voici le titre reformulé : La commune achète 2 bus','La commune achète 2 bus'),
 ('La commune achète 3 bus','Town buys 2 buses'),
 ('[Timeout]','Town buys 2 buses'),('Voici une suggestion\nUn titre','Town buys 2 buses'),
 ('La commune achète 2 bus','La commune achète 2 bus')])
def test_translation_heading_cleaning_and_fallback(tmp_path,output,expected):
    obj=qualify(make_fetcher(tmp_path))
    with patch('modules.core.llm_client.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.ollama_call',return_value=output) as call:
        assert obj._translate_title('Town buys 2 buses',source_language='en')==expected
    assert call.call_args.kwargs['operation']=='translate' and call.call_args.kwargs['cache_key']


def test_title_cache_versions_do_not_collide_with_summary():
    identity={'model':'fixture'}
    assert ed.cache_key('same','title-v1','fr','',identity,'title')!=ed.cache_key('same','summary-v1','fr','',identity,'summary')
    assert ed.cache_key('same','summary-v1','fr','',identity)!=ed.cache_key('same','summary-v2','fr','',identity)


def test_language_changes_prompt_lengths_without_erasing_unicode():
    p=ed.summary_prompt('日本の科学','東京のニュース。'*50,'science','日本語',ed.options({'fetch':{'editorial':{'prompt_version':'editorial-v2'}}}))
    assert '東京' in p and '50 à 90 mots' not in p and 'décompte français' in p


def test_topic_mismatch_is_review_signal_not_fact_verdict():
    result=ed.review('Deux fruits améliorent la santé des artères en huit semaines',
        'Un examen de dépistage compare la pression à la cheville et au bras chez les adultes. Les artères sont examinées par les médecins.',
        'Le dépistage compare la pression de la cheville et du bras chez les adultes.')
    assert result['status']=='needs_review' and 'low_title_content_overlap' in result['reasons']
    assert result['factually_verified'] is False

@pytest.mark.parametrize('source,output,flag',[
 ('Le budget annoncé est de 120 euros.','Le budget est de 130 euros.',True),
 ('La hausse est de 5 %.','La hausse est de 8 %.',True),
 ('Le budget est de 10 000 euros.','Le budget est de 10000 euros.',False),
 ('Un premier budget est de 120 euros et un autre de 130 euros.','Le second budget est de 130 euros.',False)])
def test_numeric_signals_are_bounded_by_unit_and_ambiguity(source,output,flag):
    assert ('numeric_disagreement' in ed.review('Budget de la commune',source,output)['reasons']) is flag


def test_real_local_cache_hit_ttl_and_version_miss_without_deleting_old_files(tmp_path,monkeypatch):
    from modules.core import llm_client
    config={'paths':{'data_dir':str(tmp_path),'articles_dir':str(tmp_path/'articles')},
            'llm':{'provider':'ollama','model':'fixture-model','cache':{'enabled':True}}}
    client=llm_client.OllamaClient(config)
    monkeypatch.setattr(llm_client,'_client',client)
    client._call_ollama_cli=Mock(side_effect=lambda prompt,*a:json.dumps({'status':'ok','summary':'Le conseil vote un budget.'}) if 'summary-editorial-v2' in prompt else 'Le conseil vote un budget.')
    obj=ArticleFetcher.__new__(ArticleFetcher);obj._apply_config(config);obj.log=Mock()
    title='Un budget communal';source='Le conseil vote un budget de 120 euros et publie sa décision. '*3
    assert obj._summarize(title,source,'france')==obj._summarize(title,source,'france')
    assert client._call_ollama_cli.call_count==1
    old={p.name for p in client._cache.cache_dir.iterdir()}
    qualify(obj)
    assert obj._summarize(title,source,'france')=='Le conseil vote un budget.'
    assert client._call_ollama_cli.call_count==2
    assert old<={p.name for p in client._cache.cache_dir.iterdir()}
    key=obj._summary_evidence['cache_identity'];meta=client._cache.cache_dir/(key+'.meta')
    value=json.loads(meta.read_text());value['ts']=0;meta.write_text(json.dumps(value))
    assert obj._summarize(title,source,'france')=='Le conseil vote un budget.'
    assert client._call_ollama_cli.call_count==3


def test_synthetic_review_corpus_exposes_false_positive_instead_of_hiding_it():
    from pathlib import Path
    from deploy.editorial_evaluate import evaluate
    corpus=json.loads((Path(__file__).parent/'fixtures/editorial/review_corpus.json').read_text())
    result=evaluate(corpus['cases'])
    assert result['cases']==11 and result['true_flags']==3 and result['false_flags']==1
    assert result['strict_numeric_false_blocks']==0
    assert result['factual_verification'] is False
    translated=next(r for r in result['results'] if r['id']=='translated-english')
    assert translated['flagged'] and not translated['expected_anomaly']


def test_punctuation_is_not_a_valid_translated_title():
    assert ed.clean_title('!!!!','Original valid headline')[0]=='Original valid headline'


def test_invalid_structured_response_is_not_replayed_from_cache_on_retry(tmp_path,monkeypatch):
    from modules.core import llm_client
    cfg={'paths':{'data_dir':str(tmp_path),'articles_dir':str(tmp_path/'articles')},
         'llm':{'provider':'ollama','model':'fixture-model'},
         'fetch':{'editorial':{'prompt_version':'editorial-v2'}}}
    client=llm_client.OllamaClient(cfg);monkeypatch.setattr(llm_client,'_client',client)
    client._call_ollama_cli=Mock(side_effect=['not a valid contract',json.dumps({'status':'ok','summary':'Le conseil vote un budget.'})])
    obj=ArticleFetcher.__new__(ArticleFetcher);obj._apply_config(cfg);obj.log=Mock()
    source='Le conseil vote un budget de 120 euros et publie sa décision. '*3
    with pytest.raises(ArticleAttemptError):obj._summarize('Un budget communal',source,'france')
    assert obj._summarize('Un budget communal',source,'france')=='Le conseil vote un budget.'
    assert client._call_ollama_cli.call_count==2


def test_short_but_substantive_source_is_not_rejected_by_arbitrary_word_target(tmp_path):
    obj=qualify(make_fetcher(tmp_path))
    with patch('modules.fetch.atlas_fetch.get_language',return_value='français'),patch('modules.fetch.atlas_fetch.get_fetch_timeout',return_value=30),patch('modules.fetch.atlas_fetch.ollama_call',return_value='{"status":"ok","summary":"Le conseil vote."}') as call:
        assert obj._summarize('Vote municipal','Le conseil vote.','france')=='Le conseil vote.'
    call.assert_called_once()


def test_duplicate_contract_fields_cannot_override_an_internal_refusal():
    with pytest.raises(ValueError,match='summary_invalid_contract'):
        ed.parse_summary('{"status":"insufficient_source","status":"ok","summary":"Une sortie."}', 'editorial-v2')


def test_translated_title_divergence_is_an_explainable_private_signal():
    check=ed.review('The municipal council approves a budget',
        'The municipal council approves a budget and explains the investment timetable.',
        'Le conseil municipal adopte un budget et explique le calendrier des investissements.',
        'Une usine étrangère annonce une fermeture complète')
    assert 'low_translated_title_summary_overlap' in check['reasons']
    assert check['factually_verified'] is False
