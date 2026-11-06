"""Offline contracts and synthetic PCM only. No model, network or TTS service."""
from dataclasses import replace,asdict
import io
import json
from pathlib import Path
import subprocess
import threading
from unittest.mock import Mock
import wave
import pytest
from modules.tts_lab.contracts import EngineSpec,LabError,split_text,rtf,sha,private_root
from modules.tts_lab.corpus import load,CORPUS_SHA
from modules.tts_lab.adapters import FixtureAdapter,PiperAdapter,EdgeAdapter,dependency
from modules.tts_lab.audio import inspect_wav,normalize_pcm,concatenate
from modules.tts_lab.worker import experiment,convert
from modules.tts_lab.report import comparison
from modules.tts_lab.runner import run
from modules.tts_lab.listening import blind,validate_evaluations,CRITERIA


def tone(path,voice='tone-a'):
    FixtureAdapter(EngineSpec(voice=voice)).synthesize('Texte fictif.',path);return path


def fake_conversion(source,destination):
    destination.write_bytes(source.read_bytes());return inspect_wav(destination)


def test_corpus_fixed_and_all_required_lengths():
    data=load();assert len(data['cases'])==12 and data['language']=='fr-FR'
    for count in (50,150,400,800,1500):assert next(c for c in data['cases'] if c['id']==f'longueur-{count}')['words']==count
    assert {'actualites','geopolitique','economie','technologie','noms','nombres','expression'}<={c['id'] for c in data['cases']}
    assert len(CORPUS_SHA)==64

@pytest.mark.parametrize('limit',[40,100,600,4000])
def test_segmentation_exact_text_order_and_no_truncation(limit):
    for case in load()['cases']:
        chunks=split_text(case['text'],limit)
        assert ''.join(c['text'] for c in chunks)==case['text']
        assert [c['index'] for c in chunks]==list(range(len(chunks)))
        assert all(c['text'].strip() and len(c['text'])<=limit for c in chunks)
        assert chunks==split_text(case['text'],limit)

@pytest.mark.parametrize('text',['','   ','x'*1000,'Texte\x00invalide'])
def test_invalid_or_unsplittable_text(text):
    with pytest.raises(LabError):split_text(text,40)

@pytest.mark.parametrize('seconds,duration,expected',[(1,2,.5),(2,1,2),(0,1,0)])
def test_rtf(seconds,duration,expected):assert rtf(seconds,duration)==expected

@pytest.mark.parametrize('seconds,duration',[(1,0),(-1,1),(float('nan'),1),(1,float('inf'))])
def test_rtf_refuses_invalid_measurements(seconds,duration):
    with pytest.raises(LabError):rtf(seconds,duration)


def test_fixture_is_not_speech_and_two_tones_are_distinct(tmp_path):
    a=tone(tmp_path/'a.wav');b=tone(tmp_path/'b.wav','tone-b')
    info=inspect_wav(a);assert info['duration_seconds']>0 and not info['all_silent']
    assert a.read_bytes()!=b.read_bytes()
    assert FixtureAdapter.sample_kind=='synthetic_fixture'


def test_pcm_normalization_and_ordered_reconstruction(tmp_path):
    a=tone(tmp_path/'a.wav');b=tone(tmp_path/'b.wav','tone-b');destination=tmp_path/'joined.wav'
    result=concatenate([a,b],destination)
    assert result['frames']==inspect_wav(a)['frames']+inspect_wav(b)['frames']
    with wave.open(str(destination),'rb') as stream:combined=stream.readframes(result['frames'])
    expected=b''
    for p in (a,b):
        with wave.open(str(p),'rb') as stream:expected+=stream.readframes(stream.getnframes())
    assert combined==expected
    normalized=normalize_pcm(destination,tmp_path/'normal.wav')
    assert abs(normalized['rms_dbfs']+20)<.05 and normalized['frames']==result['frames']
    assert normalized['peak']<=.951

@pytest.mark.parametrize('raw',[b'',b'fake mp3',b'RIFF0000WAVE'])
def test_audio_absent_invalid_or_zero_refused(tmp_path,raw):
    path=tmp_path/'bad.wav';path.write_bytes(raw)
    with pytest.raises(LabError):inspect_wav(path)


def test_zero_and_truncated_silent_outputs_refused(tmp_path):
    p=tmp_path/'zero.wav'
    with wave.open(str(p),'wb') as stream:stream.setparams((1,2,24000,0,'NONE','x'));stream.writeframes(b'\x00'*4800)
    assert inspect_wav(p)['all_silent']
    with pytest.raises(LabError):normalize_pcm(p,tmp_path/'normal.wav')
    p.write_bytes(p.read_bytes()[:-100])
    with pytest.raises(LabError,match='truncated'):inspect_wav(p)


def test_missing_model_wrong_checksum_and_dependency(tmp_path,monkeypatch):
    spec=EngineSpec(engine='piper',license_reviewed=True,code_version='1.8.0')
    with pytest.raises(LabError,match='model_and_config'):spec.validate()
    model=tmp_path/'model.onnx';model.write_bytes(b'synthetic mock bytes')
    files={k:{'path':str(model),'sha256':'0'*64} for k in ('model','config')}
    with pytest.raises(LabError,match='checksum'):replace(spec,files=files).validate()
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata,'version',Mock(side_effect=importlib.metadata.PackageNotFoundError()))
    with pytest.raises(LabError,match='dependency_missing'):PiperAdapter(spec)


def test_piper_adapter_loads_only_local_exact_model_cpu(tmp_path,monkeypatch):
    import sys,types
    config=tmp_path/'voice.json';config.write_text(json.dumps({'language':{'code':'fr_FR'},'num_speakers':1}))
    model=tmp_path/'voice.onnx';model.write_bytes(b'synthetic ONNX placeholder')
    files={name:{'path':str(p),'sha256':sha(p.read_bytes())} for name,p in [('model',model),('config',config)]}
    spec=EngineSpec(engine='piper',model='fr_FR-siwis-medium',version='test-revision',code_version='1.8.0',files=files,license_reviewed=True)
    spec.validate();voice=Mock();load_mock=Mock(return_value=voice)
    def synthesize(text,stream,**kwargs):stream.setparams((1,2,22050,0,'NONE','not compressed'));stream.writeframes(b'\x01\x00'*2205)
    voice.synthesize_wav.side_effect=synthesize
    monkeypatch.setitem(sys.modules,'piper',types.SimpleNamespace(PiperVoice=load_mock))
    monkeypatch.setitem(sys.modules,'piper.config',types.SimpleNamespace(PiperConfig=types.SimpleNamespace(from_dict=lambda value:value),SynthesisConfig=lambda **kw:kw))
    session=Mock();options=Mock();monkeypatch.setitem(sys.modules,'onnxruntime',types.SimpleNamespace(SessionOptions=lambda:options,ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL='sequential'),InferenceSession=session))
    monkeypatch.setattr('modules.tts_lab.adapters.dependency',lambda *a:None)
    adapter=PiperAdapter(spec);adapter.synthesize('Texte intégral.',tmp_path/'out.wav')
    assert session.call_args.kwargs['providers']==['CPUExecutionProvider']
    assert options.intra_op_num_threads==options.inter_op_num_threads==1
    assert session.call_args.args==(str(model),)
    assert voice.synthesize_wav.call_args.args[0]=='Texte intégral.'
    assert spec.public()['file_sha256']['model']==sha(model.read_bytes())
    assert str(tmp_path) not in json.dumps(spec.public())


def test_edge_requires_network_and_license_review():
    spec=EngineSpec(engine='edge',model='edge-online',voice='fr-FR-HenriNeural',code_version='7.3.1')
    with pytest.raises(LabError,match='license'):spec.validate(allow_network=True)
    with pytest.raises(LabError,match='network'):replace(spec,license_reviewed=True).validate()
    replace(spec,license_reviewed=True).validate(allow_network=True)


def test_edge_adapter_calls_exact_voice_no_silent_substitution(tmp_path,monkeypatch):
    import sys,types
    saved=[]
    class Communicate:
        def __init__(self,text,voice,**kwargs):saved.append((text,voice))
        async def save(self,path):Path(path).write_bytes(b'synthetic MP3 placeholder')
    monkeypatch.setitem(sys.modules,'edge_tts',types.SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr('modules.tts_lab.adapters.dependency',lambda *a:None)
    spec=EngineSpec(engine='edge',voice='fr-FR-DeniseNeural',code_version='7.3.1')
    EdgeAdapter(spec).synthesize('Texte sans suppression.',tmp_path/'out.mp3')
    assert saved==[('Texte sans suppression.','fr-FR-DeniseNeural')]


def test_experiment_repeats_loaded_model_and_preserves_segments(tmp_path):
    data=experiment(EngineSpec(),[load()['cases'][-1]],tmp_path,repetitions=3,converter=fake_conversion)
    assert len(data['runs'])==3 and data['sample_kind']=='synthetic_fixture'
    assert data['runs'][0]['phase']=='first_after_load' and data['runs'][1]['phase']=='loaded_model_repeat'
    for run in data['runs']:
        assert run['coverage']=='unverified_no_asr_or_listening' and run['peak_vram_mib'] is None
        assert len(run['segments'])>1 and run['rtf']>=0
        assert run['audio']['frames']==sum(s['audio']['frames'] for s in run['segments'])


def test_no_missing_segment_accepted(tmp_path):
    class Missing:
        sample_kind='real_synthesis'
        def synthesize(self,text,path):return {'native_format':'wav'}
    with pytest.raises(LabError,match='missing_native'):experiment(EngineSpec(),[load()['cases'][0]],tmp_path,adapter_factory=lambda _:Missing(),converter=fake_conversion)


def test_conversion_protocol_and_format_constraints(tmp_path,monkeypatch):
    source=tone(tmp_path/'input.wav');destination=tmp_path/'output.wav';calls=[]
    def fake(command,**kwargs):calls.append((command,kwargs));destination.write_bytes(source.read_bytes());return Mock(returncode=0)
    monkeypatch.setattr(subprocess,'run',fake);convert(source,destination)
    command,options=calls[0]
    assert command[command.index('-protocol_whitelist')+1]=='file,pipe'
    assert command[command.index('-f')+1]=='wav' and '-threads' in command and options['timeout']==15
    monkeypatch.setattr(subprocess,'run',Mock(return_value=Mock(returncode=1)))
    with pytest.raises(LabError,match='conversion'):convert(source,destination)


def test_report_missing_values_and_fixture_timings_not_tts_performance():
    report=comparison([{'engine':{'engine':'fixture'},'status':'success','result':{'sample_kind':'synthetic_fixture','runs':[{'rtf':.001}]}}])
    assert 'non mesuré' in report and '.001' not in report and 'Aucun vainqueur' in report
    assert 'Qualité humaine' in report


def test_listening_does_not_invent_scores_or_rate_fixture(tmp_path):
    path=tmp_path/'result.json';path.write_text(json.dumps({'status':'success','result':{'sample_kind':'synthetic_fixture'}}))
    with pytest.raises(LabError):blind([path],tmp_path/'blind')
    with pytest.raises(LabError):validate_evaluations({'version':1,'evaluations':[{'ratings':dict.fromkeys(CRITERIA)}]})
    result=validate_evaluations({'version':1,'evaluations':[{'ratings':dict.fromkeys(CRITERIA,3),'comment':'Écoute manuelle de test','listener':'auditeur-test'}]})
    assert 'not scientific MOS' in result['scope']


def test_private_paths_and_no_production_publication(tmp_path):
    for name in ('audio_queue','android_cache','articles','site'):
        with pytest.raises(LabError):private_root(tmp_path/name/'trial')
    path=tmp_path/'existing';path.mkdir()
    with pytest.raises(LabError):private_root(path)


def test_explicit_execution_and_fixed_corpus_required(tmp_path):
    with pytest.raises(LabError,match='explicit_execution'):run(EngineSpec(),[load()['cases'][0]],tmp_path/'run')
    with pytest.raises(LabError,match='fixed_corpus'):run(EngineSpec(),[dict(load()['cases'][0],text='changed')],tmp_path/'run',execute=True)
    cancel=threading.Event();cancel.set()
    with pytest.raises(LabError,match='cancelled'):run(EngineSpec(),[load()['cases'][0]],tmp_path/'run',execute=True,cancel=cancel)


def test_runner_redacts_error_and_cleans_partial_audio(tmp_path):
    def popen(command,**kwargs):
        assert 'SECRET_TOKEN' not in kwargs['env'] and kwargs['start_new_session'] is True
        assert kwargs['env']['CUDA_VISIBLE_DEVICES']==''
        (kwargs['cwd']/'partial.wav').write_bytes(b'partial')
        kwargs['stdout'].write(json.dumps({'status':'failed','reason':'private-token-in-provider-message'}).encode())
        process=Mock(returncode=2);process.poll.return_value=2;process.communicate.return_value=(None,None);return process
    result=run(EngineSpec(),[load()['cases'][0]],tmp_path/'run',execute=True,popen=popen)
    assert result['reason']=='worker_failure' and 'private-token' not in json.dumps(result)
    assert not list((tmp_path/'run').glob('*.wav')) and (tmp_path/'run/result.json').exists()


def test_runner_cancellation_terminates_owned_process_group(tmp_path,monkeypatch):
    cancel=threading.Event();process=Mock(pid=123456,returncode=None);process.poll.return_value=None
    def communicate(**kwargs):cancel.set();raise subprocess.TimeoutExpired('worker',.1)
    process.communicate.side_effect=communicate
    kill=Mock();monkeypatch.setattr('modules.tts_lab.runner.os.killpg',kill)
    result=run(EngineSpec(),[load()['cases'][0]],tmp_path/'cancel',execute=True,popen=lambda *a,**kw:process,cancel=cancel)
    assert result['reason']=='cancelled';kill.assert_called_once();process.wait.assert_called_once()


def test_runner_timeout_has_no_live_worker(tmp_path,monkeypatch):
    process=Mock(pid=123456,returncode=None);process.poll.return_value=None
    process.communicate.side_effect=subprocess.TimeoutExpired('worker',.1)
    times=iter([0,0,0,1000]);monkeypatch.setattr('modules.tts_lab.runner.time.monotonic',lambda:next(times))
    kill=Mock();monkeypatch.setattr('modules.tts_lab.runner.os.killpg',kill)
    result=run(EngineSpec(),[load()['cases'][0]],tmp_path/'timeout',execute=True,popen=lambda *a,**kw:process,timeout=1)
    assert result['reason']=='timeout' and kill.called


@pytest.mark.parametrize('params',[{'length_scale':0},{'length_scale':float('inf')},{'voice_reference':'private.wav'}])
def test_unsafe_synthesis_parameters_refused(params):
    spec=EngineSpec(engine='piper',code_version='1.8.0',license_reviewed=True,parameters=params)
    with pytest.raises(LabError):spec.validate()


def test_model_path_symlink_and_changed_file_refused(tmp_path):
    p=tmp_path/'weights';p.write_bytes(b'fixture');link=tmp_path/'link';link.symlink_to(p)
    spec=EngineSpec(engine='piper',code_version='1.8.0',license_reviewed=True,files={k:{'path':str(link),'sha256':sha(b'fixture')} for k in ('model','config')})
    with pytest.raises(LabError,match='unsafe_path'):spec.validate()


def test_blind_pack_masks_engine_and_keeps_empty_ratings(tmp_path):
    sample=tone(tmp_path/'normalized.wav');info=inspect_wav(sample)
    report={'status':'success','engine':{'engine':'mock-real-engine'},'result':{'sample_kind':'real_synthesis','runs':[{'comparison_file':sample.name,'normalized_audio':info,'case_id':'actualites','repetition':0}]}}
    path=tmp_path/'result.json';path.write_text(json.dumps(report))
    out=tmp_path/'blind';assert blind([path],out)['samples']==1
    form=json.loads((out/'listening-form.json').read_text())
    assert 'mock-real-engine' not in json.dumps(form) and form['evaluations'][0]['ratings']['naturel'] is None
    assert (out/'E001.wav').read_bytes()==sample.read_bytes()
    assert (out/'.private-key.json').stat().st_mode&0o777==0o600


def test_long_silence_observed_not_a_claim_of_transcript_integrity(tmp_path):
    p=tmp_path/'silence.wav'
    with wave.open(str(p),'wb') as w:w.setparams((1,2,8000,0,'NONE','x'));w.writeframes(b'\x00'*48000)
    info=inspect_wav(p);assert info['longest_silence_seconds']==3 and info['all_silent']


def test_convert_timeout_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'run',Mock(side_effect=subprocess.TimeoutExpired('ffmpeg',15)))
    with pytest.raises(LabError,match='conversion'):convert(tmp_path/'source.mp3',tmp_path/'out.wav')


def test_two_voice_lab_artifacts_are_not_production_publications(tmp_path):
    from modules.radio.audio_integrity import validated_publication
    path=tone(tmp_path/'tone.wav')
    assert not validated_publication(path) and not list(tmp_path.glob('*.ready.json'))
