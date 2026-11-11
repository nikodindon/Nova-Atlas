"""Prepare offline; measure only on an explicitly authorized isolated Ornith endpoint."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from modules.fetch import editorial as ed
from modules.core.llm_client import OllamaClient
from modules.fetch.editorial_trace import sensitive
from deploy.editorial_qualification import ExportError,read,sha,json_text,output_bundle

CORPUS=Path(__file__).resolve().parents[1]/'config/editorial_qualification/editorial-v21-corpus.json'
FOCUSED_CORPUS=CORPUS.with_name('editorial-v22-corpus.json')
FOCUSED_VERSIONS=('editorial-v2.1','editorial-v2.2')
VERSIONS=('legacy-v1','editorial-v2','editorial-v2.1')
ENDPOINT='http://127.0.0.1:18081'
PARAMETERS={'temperature':0.3,'max_tokens':4096,'seed':None,'seed_policy':'not_sent_server_default','timeout_seconds':240}


def prepare(output,repetitions=1,suite='original'):
    if suite not in ('original','v22-focused'):raise ExportError('unknown_suite')
    corpus_path,versions=(CORPUS,VERSIONS) if suite=='original' else (FOCUSED_CORPUS,FOCUSED_VERSIONS)
    if type(repetitions) is not int or not 1<=repetitions<=3:raise ExportError('repetition_limit')
    raw=read(corpus_path);corpus=json.loads(raw);jobs=[]
    for index,case in enumerate(corpus['cases']):
        # Rotate version order to avoid always warming the same variant first.
        for repetition in range(1,repetitions+1):
            offset=index%len(versions)
            order=versions[offset:]+versions[:offset]
            for version in order:
                prompt=ed.summary_prompt(case['title'],case['source'],case.get('category','qualification'),'fr',ed.options({'fetch':{'editorial':{'prompt_version':version}}}))
                jobs.append({'case_id':case['id'],'version':version,'repetition':repetition,
                    'prompt':prompt,'prompt_sha256':sha(prompt.encode()),
                    'transmitted_source_sha256':sha(case['source'][:3000].encode()),
                    'expected_status':case['expected_status'],'reference_facts':case['reference_facts']})
    protocol={'schema':1,'kind':'offline_plan_not_executed','corpus_sha256':sha(raw),'jobs':jobs,
              'parameters':PARAMETERS,'seed_controlled':False,'truth_scores':None,
              'reference_scope':corpus['reference_scope'],'model_results':None}
    if suite!='original':protocol['suite']=suite
    output_bundle(output,{'plan.json':json_text(protocol)},(corpus_path,))
    return {'status':'prepared_not_measured','cases':len(corpus['cases']),'calls':len(jobs),'network_calls':0}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise urllib.error.HTTPError(args[0].full_url,args[2],'redirect_refused',args[4],args[1])


class IsolatedClient(OllamaClient):
    def _open_http(self,request,*,timeout):
        if request.full_url not in (ENDPOINT+'/v1/models',ENDPOINT+'/v1/chat/completions'):
            raise ValueError('non_isolated_destination_refused')
        return self._isolated_opener.open(request,timeout=timeout)

    def _detect_model_from_server(self):
        self.detected=None
        request=urllib.request.Request(ENDPOINT+'/v1/models')
        with self._open_http(request,timeout=5) as response:
            raw=response.read(65537)
            if len(raw)>65536:raise ExportError('model_listing_limit')
            listing=json.loads(raw)
        if self.model in [row.get('id') for row in listing.get('data',[]) if isinstance(row,dict)]:self.detected=self.model
        return self.detected

    def __init__(self,authorization,work):
        self._isolated_opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        super().__init__({'paths':{'data_dir':str(work)},'llm':{
            'provider':'llama-server','base_url':ENDPOINT,'model':authorization['model'],
            'cache':{'enabled':False},'lock_wait_seconds':{'fetch':0}}})
        if self.detected!=authorization['model']:raise ExportError('declared_model_unavailable')


def authorization(path):
    value=json.loads(read(path,16384))
    required={'schema','approved','isolated_from_atlas_lab','endpoint','family','model','quantization',
              'weights_sha256','runtime_version','hardware','reasoning_mode','server_parameters','max_total_seconds'}
    if not isinstance(value,dict) or set(value)!=required:raise ExportError('invalid_authorization')
    if value['schema']!=1 or value['approved'] is not True or value['isolated_from_atlas_lab'] is not True or value['endpoint']!=ENDPOINT:
        raise ExportError('isolated_operator_authorization_required')
    if value['family']!='Ornith-1.5-35B' or value['quantization']!='IQ2_M' or value['reasoning_mode'] not in ('on','off'):
        raise ExportError('exact_ornith_environment_required')
    if not isinstance(value['weights_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['weights_sha256']):raise ExportError('weights_digest_required')
    for key in ('model','runtime_version','hardware'):
        if not isinstance(value[key],str) or not value[key].strip() or len(value[key])>1000:raise ExportError('environment_required')
    params=value['server_parameters']
    if not isinstance(params,dict) or set(params)!={'context_size','threads','gpu_layers'}:raise ExportError('server_parameters_required')
    if any(type(params[k]) is not int for k in params) or params['context_size']<4096 or params['threads']<1 or params['gpu_layers']<0:raise ExportError('invalid_server_parameters')
    if type(value['max_total_seconds']) is not int or not 60<=value['max_total_seconds']<=21600 or sensitive(value):raise ExportError('invalid_authorization_budget_or_content')
    return value


def validated_protocol(plan_path):
    raw=read(plan_path);protocol=json.loads(raw)
    if protocol.get('schema')!=1 or protocol.get('kind')!='offline_plan_not_executed' or protocol.get('parameters')!=PARAMETERS:
        raise ExportError('invalid_or_changed_protocol')
    jobs=protocol.get('jobs')
    if not isinstance(jobs,list) or not 1<=len(jobs)<=117:raise ExportError('bounded_jobs_required')
    suite=protocol.get('suite','original')
    if suite not in ('original','v22-focused'):raise ExportError('unknown_suite')
    corpus_path,versions=(CORPUS,VERSIONS) if suite=='original' else (FOCUSED_CORPUS,FOCUSED_VERSIONS)
    corpus=json.loads(read(corpus_path));cases={c['id']:c for c in corpus['cases']}
    if protocol.get('corpus_sha256')!=sha(read(corpus_path)):raise ExportError('corpus_changed')
    seen=set()
    for job in jobs:
        if not isinstance(job,dict) or job.get('case_id') not in cases or job.get('version') not in versions or type(job.get('repetition')) is not int or not 1<=job['repetition']<=3:raise ExportError('invalid_job')
        key=(job['case_id'],job['version'],job['repetition'])
        if key in seen:raise ExportError('duplicate_job')
        seen.add(key);case=cases[job['case_id']]
        expected=ed.summary_prompt(case['title'],case['source'],case.get('category','qualification'),'fr',ed.options({'fetch':{'editorial':{'prompt_version':job['version']}}}))
        if job.get('prompt')!=expected or job.get('prompt_sha256')!=sha(expected.encode()) or job.get('expected_status')!=case['expected_status']:raise ExportError('changed_prompt_or_reference')
    repetitions={job['repetition'] for job in jobs}
    matrix={(case,version,repetition) for case in cases for version in versions for repetition in range(1,max(repetitions)+1)}
    if seen!=matrix:raise ExportError('balanced_complete_protocol_required')
    return raw,protocol,corpus,jobs


def assess_output(job,text,receipt):
    unsafe=len(text)>64000 or bool(ed.REASONING.search(text)) or sensitive(text)
    if unsafe:text=''
    version=job['version'];syntax=None;summary=None;failure=None
    if version!='legacy-v1' and text and not unsafe:
        try:json.loads(text);syntax=True
        except (ValueError,TypeError):syntax=False
    try:summary=ed.parse_summary(text,version);status='text_requires_human_classification' if version=='legacy-v1' else 'ok'
    except ValueError as exc:
        failure='unsafe_or_oversized_output_omitted' if unsafe else receipt.get('kind') if receipt.get('kind') not in (None,'success') else str(exc)
        status='refused' if failure=='summary_source_insufficient' else 'failed'
    return {'case_id':job['case_id'],'version':version,'repetition':job['repetition'],
        'request_id':receipt.get('request_id'),'prompt_sha256':job['prompt_sha256'],'receipt':receipt,
        'output':text,'summary':summary,'status':status,'error':failure,'unsafe_output_omitted':unsafe,
        'json_syntax_valid':syntax,'reference_status':job['expected_status'],
        'unjustified_refusal_by_corpus_label':status=='refused' and job['expected_status']=='ok',
        'legitimate_refusal_by_corpus_label':status=='refused' and job['expected_status']=='insufficient_source',
        'summary_words':len(summary.split()) if summary else 0,
        'human_review':{'french_quality_0_to_4':None,'invented_information':None,'missing_facts':None,'evidence_quotes':None,'reviewer':None}}


def export_measurements(raw,protocol,corpus,jobs,auth,results,output,plan_path,authorization_path,interrupted=False):
    summaries=[]
    versions=tuple(dict.fromkeys(job['version'] for job in jobs))
    for version in versions:
        selected=[r for r in results if r['version']==version]
        times=[r['receipt'].get('response_duration_ms') for r in selected if r['receipt'].get('response_duration_ms') is not None]
        summaries.append({'version':version,'observed_runs':len(selected),
            'structured_refusals':sum(r['status']=='refused' for r in selected) if version!='legacy-v1' else None,
            'unjustified_refusals_by_corpus_label':sum(r['unjustified_refusal_by_corpus_label'] for r in selected) if version!='legacy-v1' else None,
            'legitimate_refusals_by_corpus_label':sum(r['legitimate_refusal_by_corpus_label'] for r in selected) if version!='legacy-v1' else None,
            'invalid_json_syntax':sum(r['json_syntax_valid'] is False for r in selected) if version!='legacy-v1' else None,
            'invalid_contract_or_content':sum(r['error']=='summary_invalid_contract' for r in selected),
            'mean_response_duration_ms':sum(times)/len(times) if times else None,
            'french_quality':None,'invented_information':None,'human_review_required':True})
    report={'schema':1,'kind':'operator_authorized_measurement','model_environment':auth,
            'environment_mode':auth.get('mode','isolated'),
            'model_environment_provenance':'operator_declaration plus recorded preflight; shared mode is not isolated',
            'plan_sha256':sha(raw),'parameters':PARAMETERS,'summaries':summaries,
            'cases':[{'id':c['id'],'title':c['title'],'category':c.get('category','qualification'),
                      'source_transmitted':c['source'][:3000],'expected_status':c['expected_status'],
                      'reference_facts':c['reference_facts'],'notes':c['notes']} for c in corpus['cases']],
            'runs':results,'planned_runs':len(jobs),
            'completed_runs':len(results),'complete_scope':'all planned attempts, not a quality pass',
            'finished_provider_responses':sum(r['receipt'].get('http_status')==200 and r['receipt'].get('finish_reason') in ('stop','length','tool_calls','content_filter') for r in results),
            'complete':len(results)==len(jobs),'interrupted':interrupted,
            'truth_scores':None,'human_review_required':True,'deployment_authorized':False}
    lines=['# Essai réel Ornith — revue humaine requise','','Environnement : '+auth.get('mode','isolated')+'.','','Aucune note automatique de vérité ni autorisation de déploiement.',
           f"Appels observés : {len(results)} / {len(jobs)}. Seed non envoyé ; sorties non garanties identiques.",'']
    for case in report['cases']:
        lines.extend(['## '+case['id'],'Source synthétique CC0 ; seule cette fenêtre a été transmise.',
                      '```json',json.dumps(case,ensure_ascii=False,indent=2),'```',''])
        for row in [r for r in results if r['case_id']==case['id']]:
            lines.extend(['### '+row['version']+' / répétition '+str(row['repetition']),
                          f"État : {row['status']}. JSON syntaxiquement valide : {row['json_syntax_valid']}.",
                          '```json',json.dumps({'output':row['output'],'receipt':row['receipt'],'human_review':row['human_review']},ensure_ascii=False,indent=2),'```',''])
    output_bundle(output,{'measurements.json':json_text(report),'review.md':'\n'.join(lines)+'\n'},(plan_path,authorization_path))
    return {'status':'measured_review_required','runs':len(results),'complete':report['complete'],'quality_validated':False}


def measure(plan_path,authorization_path,output,*,execute=False):
    if not execute:raise ExportError('explicit_execute_required')
    auth=authorization(authorization_path) # Validate before any model listing or generation.
    raw,protocol,corpus,jobs=validated_protocol(plan_path)
    output=Path(output)
    if output.exists() or not output.parent.is_dir() or any(p.is_symlink() for p in (output,*output.parents)):raise ExportError('new_private_output_required')
    if any((p/'.git').is_file() or (p/'.git/HEAD').is_file() for p in (output,*output.parents)):raise ExportError('private_output_outside_git_required')
    for forbidden in (Path.home()/'atlas-lab/state',Path.home()/'atlas-lab/Nova-Atlas'):
        if output.absolute().is_relative_to(forbidden) or forbidden.is_relative_to(output.absolute()):raise ExportError('active_installation_output_refused')
    results=[];deadline=time.monotonic()+auth['max_total_seconds'];interrupted=False
    with tempfile.TemporaryDirectory(prefix='nova-isolated-trial-') as work:
        client=IsolatedClient(auth,Path(work))
        for job in jobs:
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            receipt={};identity=uuid.uuid4().hex
            try:
                text=client.call(job['prompt'],caller='fetch',timeout=min(PARAMETERS['timeout_seconds'],remaining),
                                 use_cache=False,request_id=identity,metrics=receipt)
            except KeyboardInterrupt:interrupted=True;break
            results.append(assess_output(job,text,receipt))
    return export_measurements(raw,protocol,corpus,jobs,auth,results,output,plan_path,authorization_path,interrupted)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--output',type=Path,required=True);a.add_argument('--repetitions',type=int,default=1);a.add_argument('--suite',choices=('original','v22-focused'),default='original')
    a=sub.add_parser('measure');a.add_argument('--plan',type=Path,required=True);a.add_argument('--authorization',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--execute',action='store_true')
    a=sub.add_parser('measure-shared');a.add_argument('--plan',type=Path,required=True);a.add_argument('--authorization',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--execute',action='store_true');a.add_argument('--resume',action='store_true')
    args=p.parse_args()
    try:
        if args.command=='prepare':result=prepare(args.output,args.repetitions,args.suite)
        elif args.command=='measure-shared':
            from deploy.editorial_shared_trial import measure_shared
            result=measure_shared(args.plan,args.authorization,args.output,execute=args.execute,resume=args.resume)
        else:result=measure(args.plan,args.authorization,args.output,execute=args.execute)
    except (ExportError,ValueError,OSError):raise SystemExit('Trial refused or interrupted by an input/transport error; inspect configuration privately. No provider body logged.') from None
    print(json.dumps(result))

if __name__=='__main__':main()
