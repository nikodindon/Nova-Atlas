"""Fixed shipped corpus, no data from the installation or a network provider."""
import json
from pathlib import Path
import re
from modules.tts_lab.contracts import LabError,sha

PATH=Path(__file__).with_name('corpus')/'fr-v1.json'
CORPUS_SHA='76f48e7f2eecad8e0c8f59a69d1239bb6b96bb1110761b7b4c74e52324b1aeb6'


def load():
    raw=PATH.read_bytes()
    if sha(raw)!=CORPUS_SHA:raise LabError('corpus_checksum_mismatch')
    data=json.loads(raw)
    if data['version']!=1 or data['language']!='fr-FR' or len(data['cases'])!=12:raise LabError('invalid_corpus')
    ids=set()
    for case in data['cases']:
        if not re.fullmatch('[a-z0-9-]{1,40}',case['id']) or case['id'] in ids or sha(case['text'].encode())!=case['sha256'] or len(case['text'].split())!=case['words']:raise LabError('invalid_corpus_case')
        ids.add(case['id'])
    return data
