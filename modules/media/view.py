"""Template projection is local only; articles are not augmented or rewritten."""
from modules.media.store import MediaStore,FALLBACK,target_for_article,target_for_event


def helpers(paths):
    config=paths.get('_config',{}).get('media',{})
    store=MediaStore(paths['data'],mode=config.get('mode','disabled'))
    def article(value):
        if store.mode!='approved_only':return dict(FALLBACK)
        try:return store.public_for(target_for_article(value))
        except (ValueError,OSError,KeyError,TypeError):return dict(FALLBACK)
    def event(value):
        if store.mode!='approved_only':return dict(FALLBACK)
        try:return store.public_for(target_for_event(value))
        except (ValueError,OSError,KeyError,TypeError):return dict(FALLBACK)
    return {'media_for_article':article,'media_for_event':event}

FIGURE='''{% set visual = media_for_article(a) %}
<figure class="news-image"><img src="{{ visual.url }}" width="{{ visual.width }}" height="{{ visual.height }}" loading="lazy" decoding="async" alt="{{ visual.caption }}">
<figcaption>{{ visual.caption }}{% if visual.attribution %} · {{ visual.attribution.author }} · {{ visual.attribution.credit }} · <a href="{{ visual.attribution.source|safe_url }}" rel="noopener noreferrer">Source</a> · <a href="{{ visual.attribution.license_url|safe_url }}" rel="license noopener noreferrer">{{ visual.attribution.license }}</a> · {{ visual.attribution.changes }}{% endif %}</figcaption></figure>'''
