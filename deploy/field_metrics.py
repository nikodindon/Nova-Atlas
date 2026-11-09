"""Read-only bounded metrics; no Docker commands, cmdline, environment or data reads."""
import argparse,json,os,signal,stat,time
from datetime import datetime,timezone
from pathlib import Path
import threading
from deploy.recovery import checked

MAX_REPORT=32*1024*1024

def process_sample(pid,*,proc=Path('/proc')):
    root=proc/str(pid)
    if root.stat().st_uid!=os.getuid():raise ValueError('own_processes_only')
    # Strip comm including parentheses; never emit names or command lines.
    parts=(root/'stat').read_text().rsplit(')',1)[1].split()
    return {'pid':pid,'start_ticks':int(parts[19]),'cpu_ticks':int(parts[11])+int(parts[12]),'rss_bytes':int(parts[21])*os.sysconf('SC_PAGE_SIZE'),'state':parts[0]}

def sample(storage,pids=(),*,proc=Path('/proc')):
    mem={line.split(':')[0]:int(line.split()[1])*1024 for line in (proc/'meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:'))}
    ticks=[int(v) for v in (proc/'stat').read_text().splitlines()[0].split()[1:9]]
    disk=os.statvfs(storage);processes=[]
    for pid in pids:
        try:processes.append(process_sample(pid,proc=proc))
        except (ValueError,OSError,IndexError):processes.append({'pid':pid,'status':'NOT_TESTED','reason':'missing_reused_or_inaccessible_process'})
    return {'cpu_total_ticks':sum(ticks),'cpu_idle_ticks':ticks[3]+ticks[4],'memory':mem,'free_bytes':disk.f_bavail*disk.f_frsize,'processes':processes}

def run(collector,output,*,duration=0,interval=10,long_authorized=False,resources_reserved=False,clock=time.monotonic,stop=None,wall=lambda:datetime.now(timezone.utc).isoformat()):
    if not 0<=duration<=86400 or not 1<=interval<=300 or duration>60 and not (long_authorized and resources_reserved):raise ValueError('explicit_long_run_authorization_required')
    path=checked(output);stop=stop or threading.Event();start=clock();count=0;written=0;previous=None;identities={}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        def emit(row):
            nonlocal written
            raw=(json.dumps(row,sort_keys=True)+'\n').encode();written+=len(raw)
            if written>MAX_REPORT:raise ValueError('report_quota')
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        emit({'version':1,'kind':'start','date':wall(),'requested_seconds':duration,'interval_seconds':interval,'long_run_operator_acknowledged':long_authorized,'resources_reserved':resources_reserved,'classification':'observations_not_uptime_or_physical_qualification'})
        while not stop.is_set():
            current=collector();elapsed=clock()-start
            for row in current.get('processes',[]):
                if 'start_ticks' in row:
                    old=identities.setdefault(row['pid'],row['start_ticks'])
                    if old!=row['start_ticks']:row.clear();row.update(status='NOT_TESTED',reason='pid_reused')
            if previous is not None and 'cpu_total_ticks' in current:
                total=current['cpu_total_ticks']-previous['cpu_total_ticks'];idle=current['cpu_idle_ticks']-previous['cpu_idle_ticks']
                current['host_cpu_busy_fraction']=max(0,min(1,1-idle/total)) if total>0 else None
            emit({'version':1,'kind':'sample','date':wall(),'elapsed_seconds':elapsed,'observation':current});count+=1;previous=current
            if clock()-start>=duration:break
            stop.wait(min(interval,max(0,duration-(clock()-start))))
        emit({'version':1,'kind':'end','date':wall(),'elapsed_seconds':clock()-start,'samples':count,'reason':'interrupted' if stop.is_set() else 'duration_reached','endurance_qualified':False})
    return {'status':'observed','samples':count,'endurance_qualified':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--storage',required=True);p.add_argument('--output',required=True);p.add_argument('--pid',type=int,action='append',default=[]);p.add_argument('--duration',type=int,default=0);p.add_argument('--interval',type=int,default=10);p.add_argument('--authorize-long-run',action='store_true');p.add_argument('--resources-reserved',action='store_true');a=p.parse_args();stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    try:
        root=checked(a.storage)
        if not root.is_dir() or len(a.pid)>8 or any(v<=0 for v in a.pid):raise ValueError()
        result=run(lambda:sample(root,a.pid),a.output,duration=a.duration,interval=a.interval,long_authorized=a.authorize_long_run,resources_reserved=a.resources_reserved,stop=stop)
    except (ValueError,OSError):result={'status':'FAIL','reason':'metrics_refused_or_interrupted_storage_preserved'}
    print(json.dumps(result));raise SystemExit(1 if result['status']=='FAIL' else 0)
if __name__=='__main__':main()
