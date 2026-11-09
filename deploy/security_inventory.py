"""Pinned CI-only SBOM and CVE inventory. Plan locally; never starts a service."""
import argparse,collections,hashlib,io,json,os,re,subprocess,tarfile,urllib.request
from pathlib import Path
TOOLS={
 'syft':('1.55.0','bb65249542db5fedeeb4d3f9b018b75e7264dbfbbb10486f044581a92f90490d'),
 'grype':('0.121.2','9c8965ced9df657e5e851c14021bae838802f711edd7a48f9bc3c923a8d28f12')}
TARGETS={'runtime':('Dockerfile','.'),'frontend':('frontend/Dockerfile','frontend'),'nginx':('deploy/frontend/Dockerfile','.'),'icecast':('deploy/Dockerfile.icecast','.'),'litellm':('deploy/Dockerfile.litellm','.'),'postgres':None}
ROOT=Path(__file__).resolve().parents[1]

def install(directory):
    directory.mkdir(mode=0o700)
    for name,(version,sha) in TOOLS.items():
        url=f'https://github.com/anchore/{name}/releases/download/v{version}/{name}_{version}_linux_amd64.tar.gz'
        with urllib.request.urlopen(url,timeout=60) as response:raw=response.read(100*1024*1024+1)
        if len(raw)>100*1024*1024 or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('tool_checksum_mismatch')
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
            member=archive.getmember(name)
            if not member.isfile() or member.size>200*1024*1024:raise ValueError('invalid_tool_archive')
            body=archive.extractfile(member).read()
        path=directory/name
        with path.open('xb') as out:out.write(body)
        path.chmod(0o700)
    return {name:str((directory/name).resolve()) for name in TOOLS}

def summarize(report):
    matches=report.get('matches')
    if not isinstance(matches,list) or not isinstance(report.get('descriptor'),dict):raise ValueError('invalid_scan_report')
    severity=collections.Counter(m.get('vulnerability',{}).get('severity','Unknown') for m in matches)
    return {'status':'WARNING' if matches else 'PASS_WITHIN_SCANNER_COVERAGE','matches':len(matches),'severity':dict(severity),
        'classification':'potential_pending_applicability_review' if matches else 'no_matches_in_this_database_not_a_security_guarantee',
        'automatic_suppressions':0,'public_release_authorized':False}

def lock_bom(lock):
    from urllib.parse import quote
    components=[]
    for key,row in lock.get('packages',{}).items():
        if not key or not isinstance(row.get('version'),str):continue
        name=key.rsplit('node_modules/',1)[-1]
        components.append({'type':'library','name':name,'version':row['version'],'purl':'pkg:npm/'+quote(name,safe='/')+'@'+quote(row['version'],safe='')})
    return {'bomFormat':'CycloneDX','specVersion':'1.6','version':1,'components':components}

def run(target,output):
    if os.environ.get('GITHUB_ACTIONS')!='true':raise ValueError('disposable_ci_runner_only')
    output=Path(output)
    if output.exists() or output.is_symlink():raise ValueError('new_output_required')
    output.mkdir(mode=0o700);tool=install(output/'tools')
    def command(args,*,capture=False,timeout=900):
        value=subprocess.run(args,check=True,stdout=subprocess.PIPE if capture else None,timeout=timeout,text=True)
        return value.stdout if capture else ''
    versions={name:json.loads(command([path,'version','-o','json'],capture=True,timeout=30)) for name,path in tool.items()}
    commit=command(['git','rev-parse','HEAD'],capture=True,timeout=10).strip()
    if target=='postgres':
        text=(ROOT/'docker-compose.database.yml').read_text();image=re.search(r'image:\s*(\S+)',text)[1]
        source='registry:'+image;identity=image
    else:
        dockerfile,context=TARGETS[target];image=f'atlas-s15-{target}:{commit[:12]}'
        command(['docker','build','-f',dockerfile,'-t',image,context],timeout=1200)
        source='docker:'+image;identity=command(['docker','image','inspect','--format','{{.Id}}',image],capture=True,timeout=20).strip()
    command([tool['syft'],source,'-o',f'syft-json={output}/sbom.syft.json','-o',f'cyclonedx-json={output}/sbom.cdx.json','--quiet'],timeout=1200)
    sbom=json.loads((output/'sbom.syft.json').read_text())
    if not sbom.get('artifacts'):raise ValueError('empty_sbom')
    command([tool['grype'],'sbom:'+str(output/'sbom.syft.json'),'-o','json','--file',str(output/'grype.json')],timeout=1200)
    database=json.loads(command([tool['grype'],'db','status','-o','json'],capture=True,timeout=30))
    report=json.loads((output/'grype.json').read_text());summary=summarize(report)
    supplemental=None
    if target=='frontend':
        lock=ROOT/'frontend/package-lock.json'
        (output/'frontend-build-graph.cdx.json').write_text(json.dumps(lock_bom(json.loads(lock.read_text()))))
        command([tool['grype'],'sbom:'+str(output/'frontend-build-graph.cdx.json'),'-o','json','--file',str(output/'frontend-build-graph.grype.json')],timeout=1200)
        supplemental={'lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),'scope':'entire declared build graph including dev dependencies; not a precise bundle reachability map','summary':summarize(json.loads((output/'frontend-build-graph.grype.json').read_text()))}

    metadata={'schema_version':1,'target':target,'commit':commit,'image_identity':identity,'sbom_packages':len(sbom['artifacts']),'tools':versions,'database':database,'summary':summary,
        'frontend_build_graph':supplemental,'scope':'candidate final image, OS/Python/Node/native packages discoverable by Syft; no user volumes or live deployment','limitations':['scanner catalog coverage is not completeness','matches are potential until triaged','not a license grant','not a runtime reachability proof']}
    metadata['artifacts']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.json')}
    (output/'summary.json').write_text(json.dumps(metadata,indent=2)+'\n');print(json.dumps({'target':target,'packages':metadata['sbom_packages'],**summary}))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--target',choices=sorted(TARGETS));p.add_argument('--output');p.add_argument('--execute',action='store_true');a=p.parse_args()
    if not a.execute:print(json.dumps({'status':'PLAN_ONLY','tools':TOOLS,'targets':sorted(TARGETS),'host_docker_authorized':False}));return
    if not a.target or not a.output:p.error('explicit target and new output required')
    try:run(a.target,a.output)
    except (ValueError,OSError,subprocess.SubprocessError):print(json.dumps({'status':'FAIL','reason':'inventory_incomplete_not_clean'}));raise SystemExit(1)
if __name__=='__main__':main()
