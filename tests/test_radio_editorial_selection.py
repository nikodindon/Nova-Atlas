import hashlib
from datetime import datetime

from modules.fetch.editorial import review
from modules.radio.editorial_selection import ZONE, select_for_radio, wait_for_airtime


def test_radio_distinguishes_conflicting_event_from_multitopic_article():
    title = 'Une élection municipale à Valmer'
    hockey = 'Le club de hockey a remporté son match 3 à 2.'
    evidence = review(title, hockey)['evidence']
    assert evidence[0]['rule'] == 'municipal_election_vs_standalone_match_v1'
    assert not review(title, hockey + ' Les bureaux de vote ouvriront dimanche.')['evidence']
    now = datetime(2026, 11, 27, 16, 0, tzinfo=ZONE)
    blocked = {'hash':'x','summary':hockey,'pub_date':'2026-11-27T15:55:00+01:00'}
    older = {'hash':'y','summary':'Article sur un vote.','pub_date':'Fri, 27 Nov 2026 13:00:00 GMT'}
    unknown = {'hash':'z','summary':'Une source sans heure.','pub_date':'2026-11-27'}
    records = {'x':{'editorial_review':{
        'summary_sha256':hashlib.sha256(hockey.encode()).hexdigest(),'evidence':evidence,
    }}}
    options = dict(now=now,minimum=1,limit=5,fresh_minutes=30,recap_minutes=360,records=records)
    assert select_for_radio([blocked, older, unknown], **options) == ([older], 'recap')
    assert select_for_radio([blocked, unknown], **options) == ([], 'not_enough_recent_articles')
    assert select_for_radio([older, dict(blocked,hash='different')], **options)[1] == 'fresh'


def test_ready_bulletin_waits_for_time_and_handles_shutdown():
    class Stop:
        def __init__(self,cancel=False):
            self.calls=[];self.cancel=cancel
        def is_set(self):return False
        def wait(self,seconds):self.calls.append(seconds);return self.cancel
    before = datetime(2026, 11, 27, 15, 59, 58, tzinfo=ZONE)
    at = datetime(2026, 11, 27, 16, 0, tzinfo=ZONE)
    ticks = iter([before,before,at])
    stop = Stop()
    assert wait_for_airtime('2026-11-27T16:00',stop,clock=lambda: next(ticks))
    assert stop.calls == [2]
    assert not wait_for_airtime('2026-11-27T16:00',Stop(True),clock=lambda: before)
