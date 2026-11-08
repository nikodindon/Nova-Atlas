"""Bounded synthetic release exercise. No Docker, network, model or user installation."""
import argparse
from datetime import datetime,timezone,timedelta
import hashlib
import json
import os
from pathlib import Path
import resource
import tempfile
import time
from deploy.init import initialize
from deploy.recovery import snapshot,restore,inventory
from deploy.preflight import check
from modules.editorial.index import EventIndex
from modules.radio.studio import StudioJournal
from modules.radio.audio_integrity import publish_audio,AudioInfo,validated_publication
from modules.web.atlas_web import create_app
from modules.media.store import MediaStore,target_for_article



def seed_media(root,rows):
    from modules.media.store import MediaStore,target_for_article
    from modules.media.candidates import ImageCandidate
    from modules.media.worker import prepare
    from modules.media.rights import CC_BY
    from PIL import Image
    import io
    raster=io.BytesIO();Image.new('RGB',(64,36),'navy').save(raster,'PNG');raw=raster.getvalue()
    media=MediaStore(root/'data',mode='approved_only',runner=prepare)
    for i,row in enumerate(rows[:100]):
        target=target_for_article(row)
        proof={'provider':'commons','page_revision':1,'file_sha1':hashlib.sha1(raw).hexdigest(),'size':len(raw),'source_page':'https://commons.wikimedia.org/wiki/File:Synthetic_RC_fixture.png','license':'CC BY 4.0','license_url':CC_BY,'author':'Synthetic fixture author','credit':'Generated geometry fixture','attribution_required':True,'copyrighted':True}
        candidate=ImageCandidate(f'https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic_RC_{i}.png','commons',proof['source_page'],'Fixture','commons',str(i+1),proof)
        identity=media.add(candidate,target,trusted=True)
        if i==0:
            media.prepare(identity,raw,expected=media.load()[1]);record=media.load()[0]['records'][identity]
            media.decide(identity,'approve',expected=media.load()[1],binding=media.binding(record),ack=True)
    projection=media.public_for(target_for_article(rows[0]));assert not projection['fallback']
    return projection

def run(*,articles=1200,sources=240):
    if not 1<=articles<=2000 or not 1<=sources<=300:raise ValueError('bounded_synthetic_workload_required')
    start=time.perf_counter();cpu=time.process_time()
    with tempfile.TemporaryDirectory(prefix='atlas-rc-') as directory:
        base=Path(directory);root=base/'installation';initialize(root,'synthetic-rc-admin','synthetic-rc-password-not-a-provider-credential')
        day=datetime.now(timezone.utc).strftime('%Y%m%d');stamp=datetime.now(timezone.utc).isoformat()
        (root/'config/feeds.yaml').write_text('rc:\n'+''.join(f'  - https://source{i}.invalid/rss\n' for i in range(sources)))
        rows=[{'hash':hashlib.md5(str(i).encode()).hexdigest(),'title':f'Synthetic event {i}: station opens route {i}','title_original':f'Synthetic {i}','summary':f'Synthetic report number {i}. No fetched or generated content.','source':f'Fixture {i%20}','category':'rc','link':f'https://source{i%20}.invalid/article/{i}','timestamp':stamp,'pub_date':stamp} for i in range(articles)]
        for offset in range(1,6):
            old=datetime.now(timezone.utc)-timedelta(days=offset)
            historical=[{**row,'hash':hashlib.md5(f'history-{offset}-{i}'.encode()).hexdigest(),'timestamp':old.isoformat(),'pub_date':old.isoformat(),'link':f'https://fixture.invalid/history/{offset}/{i}'} for i,row in enumerate(rows[:200])]
            (root/f'data/articles/{old:%Y%m%d}_articles.json').write_text(json.dumps(historical))
        (root/f'data/articles/{day}_articles.json').write_text(json.dumps(rows));(root/f'data/reports/{day}_report.md').write_text('# Fixture report\n\nSynthetic **content**, not an editorial publication.\n')
        (root/'data/article_processing.json').write_text('{"version":1,"records":{}}')
        event_limit='not_reached'
        if articles>500:
            try:EventIndex(root/'data/articles',root/'data').rebuild()
            except ValueError:event_limit='refused_over_500_preserved_no_partial_index'
            else:raise AssertionError('event budget not enforced')
        event_input=root/'data/rc_event_input';event_input.mkdir()
        (event_input/f'{day}_articles.json').write_text(json.dumps(rows[:240]))
        index=EventIndex(event_input,root/'data');began=time.perf_counter();result=index.rebuild();index.rebuild(apply=True,expected=result['input_sha']);event_seconds=time.perf_counter()-began
        projection=seed_media(root,rows)
        journal=StudioJournal(root/'data',root/'audio_queue');job=journal.admit('2026-11-08T12:30');journal.start(job['id'])
        # Receipt/hash fixture only. Real decoded MP3s are covered by radio_studio_smoke.
        stage=root/'audio_queue/staging.mp3';stage.write_bytes(b'synthetic receipt roundtrip fixture, never enqueued')
        audio=publish_audio(stage,journal.path_for(job),AudioInfo(1),1);journal.attach(job['id'],audio)
        article_bytes=(root/f'data/articles/{day}_articles.json').read_bytes()
        from modules.fetch.feed_parser import parse_feed
        rss=b'<rss version="2.0"><channel><title>Fixture</title><item><title>Fixture</title><link>https://fixture.invalid/a</link></item></channel></rss>'
        began=time.perf_counter()
        for i in range(sources):assert parse_feed(rss,f'https://source{i}.invalid/rss',f'https://source{i}.invalid/rss').entries
        rss_seconds=time.perf_counter()-began
        probe=check(root,inspect_data=True,min_free=0)
        assert not any(row['status']=='fail' for row in probe['checks'])
        before=inventory(root,hashes=True);began=time.perf_counter();snapshot(root,base/'snapshot',apply=True,stopped=True,include_secrets=True);backup_seconds=time.perf_counter()-began
        began=time.perf_counter();restore(base/'snapshot',base/'restored',apply=True);restore_seconds=time.perf_counter()-began
        restored=base/'restored';assert inventory(restored,hashes=True)==before
        assert (restored/f'data/articles/{day}_articles.json').read_bytes()==article_bytes
        assert validated_publication(restored/'audio_queue'/audio.name)
        assert StudioJournal(restored/'data',restored/'audio_queue').load()[0]['jobs'][job['id']]['status']=='ready'
        restored_media=MediaStore(restored/'data',mode='approved_only')
        assert restored_media.public_for(target_for_article(rows[0]))==projection
        from unittest.mock import patch
        with patch.dict(os.environ,{'ATLAS_ADMIN_USERNAME':'','ATLAS_ADMIN_PASSWORD_HASH':'','ATLAS_SESSION_SECRET':''}):app=create_app({'media':{'mode':'approved_only'}},root=restored)
        client=app.test_client();latencies=[]
        for page in range(1,11):
            began=time.perf_counter();response=client.get(f'/api/public/articles?date={day}&page={page}&per_page=50',base_url='https://localhost');latencies.append(time.perf_counter()-began)
            assert response.status_code==200 and response.json['total']==articles and 'csrf_token' not in response.text
        assert client.get('/api/admin/sources',base_url='https://localhost').status_code==401
        assert client.get('/api/articles?date='+day,base_url='https://localhost').status_code==200
        from concurrent.futures import ThreadPoolExecutor
        def concurrent_read(_):
            with app.test_client() as thread_client:return thread_client.get(f'/api/public/articles?date={day}&per_page=20',base_url='https://localhost').status_code
        began=time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:assert all(status==200 for status in pool.map(concurrent_read,range(12)))
        concurrent_seconds=time.perf_counter()-began
        report={'version':1,'scope':'synthetic_single_process_flask_test_client_no_production','articles':articles,'historical_articles':articles+5*min(articles,200),'sources':sources,'derived_events':len(result['events']),'event_input_limit':event_limit,'image_candidates':min(articles,100),'approved_images':1,
            'snapshot_entries':len(before),'snapshot_files':sum(row['kind']=='file' for row in before.values()),'snapshot_bytes':sum(v['size'] for v in before.values()),'snapshot_seconds':backup_seconds,'restore_seconds':restore_seconds,'event_rebuild_seconds':event_seconds,'rss_parse_seconds':rss_seconds,'rss_network_seconds':'not_tested','concurrent_requests':12,'concurrent_workers':4,'concurrent_seconds':concurrent_seconds,
            'public_requests':len(latencies),'public_request_mean_seconds':sum(latencies)/len(latencies),'public_request_max_seconds':max(latencies),'elapsed_seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu,
            'process_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'restoration':'pass_all_file_hashes_modes_mtimes_ids_and_receipt','audio_decode':'not_tested_receipt_fixture_only','actual_radio':'not_tested','multi_machine':'not_tested','machine_a':'not_tested','machine_b':'not_tested','24_hour':'not_tested'}
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output');parser.add_argument('--articles',type=int,default=1200);parser.add_argument('--sources',type=int,default=240);args=parser.parse_args()
    report=run(articles=args.articles,sources=args.sources);raw=json.dumps(report,sort_keys=True,indent=2)+'\n'
    if args.output:
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out:out.write(raw)
    print(raw)

if __name__=='__main__':main()
