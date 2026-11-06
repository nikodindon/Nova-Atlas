"""Bounded subprocess ownership, cooperative cancellation and atomic experiment result."""
from dataclasses import asdict
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
from modules.tts_lab.contracts import EngineSpec,LabError,checked,private_root
from modules.core.source_manager import atomic_write


def hardware(*,probe_gpu=False):
    """Public technical metadata only: no hostname, username, serial or environment."""
    cpu='unknown';ram=None
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):cpu=line.split(':',1)[1].strip();break
        ram=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))
    except (OSError,ValueError,StopIteration):pass
    gpu=None
    if probe_gpu:
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.total','--format=csv,noheader,nounits'],
                capture_output=True,text=True,timeout=5,env={'PATH':os.defpath})
            if result.returncode==0:
                gpu=[]
                for line in result.stdout.splitlines()[:4]:
                    name,driver,memory=(part.strip() for part in line.split(','));gpu.append({'name':name,'driver':driver,'total_vram_mib':int(memory)})
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    return {'cpu':cpu,'total_ram_kib':ram,'gpu_inventory':gpu,'gpu_inventory_status':'queried' if probe_gpu else 'not_measured',
            'os':platform.system(),'kernel':platform.release(),'architecture':platform.machine(),'python':platform.python_version(),
            'inference_mode':'cpu' ,'worker_cpu_affinity':'one available logical CPU; nice +10','profile_match':
            'A' if 'Ryzen 5 1600' in cpu else 'B' if 'i5-8300H' in cpu else 'other_not_reference_machine'}


def stop(process):
    if process.poll() is None:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=2)


def run(spec,cases,output,*,execute=False,allow_network=False,repetitions=3,timeout=120,limit=600,cancel=None,probe_gpu=False,python=None,popen=None):
    if not execute:raise LabError('explicit_execution_required')
    if type(repetitions) is not int or not 1<=repetitions<=5 or type(timeout) not in (int,float) or not 0<timeout<=300:raise LabError('invalid_experiment_budget')
    spec.validate(allow_network=allow_network)
    if not isinstance(cases,list) or not 1<=len(cases)<=12:raise LabError('invalid_cases')
    from modules.tts_lab.corpus import load
    shipped={c['id']:c for c in load()['cases']}
    if len({c.get('id') for c in cases})!=len(cases) or any(c!=shipped.get(c.get('id')) for c in cases):raise LabError('fixed_corpus_required')
    from modules.tts_lab.contracts import split_text
    for case in cases:split_text(case['text'],limit)
    if cancel and cancel.is_set():raise LabError('cancelled')
    root=private_root(output);environment=hardware(probe_gpu=probe_gpu)
    if spec.engine=='edge':environment['inference_mode']='remote_service'
    request={'spec':asdict(spec),'cases':cases,'repetitions':repetitions,'limit':limit,'allow_network':allow_network}
    project=Path(__file__).resolve().parents[2]
    # Only this explicit isolated interpreter may load optional dependencies.
    executable=str(Path(python).resolve(strict=True)) if python else sys.executable
    if not Path(executable).is_file():raise LabError('interpreter_missing')
    env={'PATH':os.environ.get('PATH',os.defpath),'PYTHONPATH':str(project),'PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8',
         'HOME':str(root),'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','CUDA_VISIBLE_DEVICES':'',
         'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    result={'version':1,'status':'failed','reason':'worker_failure','created_at':datetime.now(timezone.utc).isoformat(),'environment':environment,'engine':spec.public()}
    process=None
    try:
        with tempfile.TemporaryFile(dir=root) as stdout:
            process=(popen or subprocess.Popen)([executable,'-m','modules.tts_lab.worker'],cwd=root,env=env,stdin=subprocess.PIPE,stdout=stdout,stderr=subprocess.DEVNULL,start_new_session=True)
            until=time.monotonic()+timeout;first=True
            while True:
                if cancel and cancel.is_set():raise LabError('cancelled')
                if time.monotonic()>=until:raise LabError('timeout')
                try:
                    process.communicate(input=json.dumps(request).encode() if first else None,timeout=min(.1,max(.001,until-time.monotonic())));break
                except subprocess.TimeoutExpired:first=False
            stdout.seek(0);raw=stdout.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise LabError('worker_report_too_large')
            try:data=json.loads(raw)
            except (ValueError,RecursionError):raise LabError('invalid_worker_report') from None
            if data.get('status')!='success' or process.returncode:
                allowed={'dependency_missing','dependency_version_mismatch','model_language_mismatch','single_speaker_model_required','conversion_failed','silent_output','missing_native_audio','engine_or_audio_failure','network_forbidden'}
                raise LabError(data.get('reason') if data.get('reason') in allowed else 'worker_failure')
            result.update(status='success',reason=None,result=data['result'])
    except LabError as error:result.update(reason=str(error))
    except (OSError,ValueError):result.update(reason='worker_unavailable')
    finally:
        if process is not None:stop(process)
        # A failed experiment leaves its report, never incomplete or raw audio.
        if result['status']!='success':
            for path in root.iterdir():
                if path.is_file() and path.suffix in ('.wav','.mp3'):path.unlink()
        atomic_write(root/'result.json',json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False))
    return result
