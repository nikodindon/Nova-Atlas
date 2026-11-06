"""Opt-in lab CLI. Dry-run by default, no models downloaded or services started."""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
from modules.tts_lab.contracts import EngineSpec,LabError,checked
from modules.tts_lab.corpus import load,CORPUS_SHA


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('run');p.add_argument('--engine',choices=('fixture','piper','edge'),default='fixture');p.add_argument('--spec',type=Path)
    p.add_argument('--output',type=Path);p.add_argument('--execute',action='store_true');p.add_argument('--allow-network',action='store_true')
    p.add_argument('--case',action='append');p.add_argument('--repetitions',type=int,default=3);p.add_argument('--timeout',type=float,default=120)
    p.add_argument('--chunk-chars',type=int,default=600);p.add_argument('--python',type=Path);p.add_argument('--probe-gpu',action='store_true')
    p=sub.add_parser('report');p.add_argument('results',nargs='*',type=Path)
    p=sub.add_parser('blind');p.add_argument('results',nargs='+',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--seed',type=int,default=0)
    p=sub.add_parser('validate-listening');p.add_argument('form',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='report':
            from modules.tts_lab.report import comparison
            print(comparison([json.loads(checked(p).read_text()) for p in args.results]));return 0
        if args.action=='blind':
            from modules.tts_lab.listening import blind
            print(json.dumps(blind(args.results,args.output,seed=args.seed)));return 0
        if args.action=='validate-listening':
            from modules.tts_lab.listening import validate_evaluations
            print(json.dumps(validate_evaluations(json.loads(checked(args.form).read_text()))));return 0
        data=load();cases=data['cases'];requested=args.case or ['longueur-50']
        if set(requested)-{c['id'] for c in cases}:raise LabError('unknown_corpus_case')
        cases=[c for c in cases if c['id'] in requested]
        if not args.execute:
            print(json.dumps({'dry_run':True,'engine':args.engine,'corpus_sha256':CORPUS_SHA,'cases':[{k:c[k] for k in ('id','words','sha256')} for c in cases],'note':'No model or operator configuration read, no network request; --execute and new --output required.'}));return 0
        if args.spec:
            path=checked(args.spec)
            if path.stat().st_size>16384:raise LabError('spec_too_large')
            spec=EngineSpec(**json.loads(path.read_text()))
            if spec.engine!=args.engine:raise LabError('engine_spec_mismatch')
        elif args.engine=='fixture':spec=EngineSpec()
        else:raise LabError('explicit_engine_spec_required')
        if not args.output:raise LabError('explicit_output_required')
        from modules.tts_lab.runner import run
        import signal,threading
        cancel=threading.Event()
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:cancel.set())
        result=run(spec,cases,args.output,execute=True,allow_network=args.allow_network,repetitions=args.repetitions,timeout=args.timeout,
            limit=args.chunk_chars,python=args.python,probe_gpu=args.probe_gpu,cancel=cancel)
        print(json.dumps({'status':result['status'],'reason':result['reason'],'sample_kind':result.get('result',{}).get('sample_kind')}));return 0 if result['status']=='success' else 2
    except LabError as error:
        print(json.dumps({'status':'refused','reason':str(error)}));return 2
    except (ValueError,OSError,TypeError,KeyError,RecursionError):
        print(json.dumps({'status':'refused','reason':'invalid_or_unavailable_lab_configuration'}));return 2

if __name__=='__main__':raise SystemExit(main())
