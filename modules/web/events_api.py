"""Read-only public projection of an explicitly built derived index."""
from flask import request,jsonify
from modules.editorial.index import EventIndex,IndexError,public_event


def install_events_api(app,paths):
    index=EventIndex(paths['articles'],paths['data'])
    app.extensions['atlas_event_index']=index

    @app.get('/api/events')
    def api_events():
        if set(request.args)-{'page','per_page','category'} or any(len(request.args.getlist(k))!=1 for k in request.args):return jsonify(status='error',msg='Paramètres invalides'),400
        try:
            page=int(request.args.get('page','1'));size=int(request.args.get('per_page','20'));category=request.args.get('category')
            if not 1<=page<=10000 or not 1<=size<=50 or (category is not None and len(category)>100):raise ValueError()
        except ValueError:return jsonify(status='error',msg='Pagination invalide'),400
        try:data=index.load()
        except (IndexError,OSError,KeyError,TypeError):return jsonify(status='unavailable',reason='index_requires_operator_review',events=[],count=0),503
        if data is None:return jsonify(status='unavailable',reason='index_not_built',events=[],count=0)
        rows=[e for e in data['events'] if not category or category in [e['category'],*e['secondary_categories']]]
        rows.sort(key=lambda e:(e.get('latest_published_at') or '',e['event_id']),reverse=True)
        return jsonify(status='ok',index_status=data['status'],generated_at=data['generated_at'],events=[public_event(e) for e in rows[(page-1)*size:page*size]],count=len(rows),page=page,per_page=size)

    @app.get('/api/events/<event_id>')
    def api_event_detail(event_id):
        try:data=index.load()
        except (IndexError,OSError,KeyError,TypeError):return jsonify(status='unavailable',reason='index_requires_operator_review'),503
        event=next((e for e in (data or {}).get('events',[]) if e['event_id']==event_id),None)
        if event is None:return jsonify(status='error',msg='Événement absent'),404
        return jsonify(status='ok',event=public_event(event))
