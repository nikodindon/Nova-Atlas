"""Strict container configuration. Legacy Python installs retain their loader."""
import copy
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit
import yaml

ROOT=Path('/app')
SECRETS=Path('/run/secrets')
PID_FILE=Path('/tmp/atlas-supervisor.json')
PATHS={'data_dir':'data','articles_dir':'data/articles','reports_dir':'data/reports',
       'editions_dir':'data/editions','posts_dir':'data/posts','site_dir':'site',
       'audio_queue':'audio_queue','tmp_dir':'tmp','background_music':'background_music','music':'music'}


class DeploymentError(ValueError):
    pass


def read_secret(name):
    path=SECRETS/name
    try:
        mode=path.stat()
        if path.is_symlink() or not stat.S_ISREG(mode.st_mode) or not 1 <= mode.st_size <= 4096:
            raise DeploymentError('Invalid secret file')
        if mode.st_mode & 0o077:
            raise DeploymentError('Secret permissions must be owner-only')
        value=path.read_text().strip()
        if not value or '\n' in value or '\r' in value:
            raise DeploymentError('Invalid secret value')
        return value
    except OSError:
        raise DeploymentError('Required secret unavailable') from None


def trusted_url(value):
    try:
        parsed=urlsplit(value)
        _ = parsed.port
        return (isinstance(value,str) and parsed.scheme in ('http','https') and bool(parsed.hostname)
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                and not any(c.isspace() for c in value) and '\\' not in value)
    except (ValueError,TypeError):
        return False


def load_container_config(path):
    from modules.core.config import DEFAULT_CONFIG
    role=os.environ.get('ATLAS_ROLE')
    if role not in ('web','news','radio'):
        raise DeploymentError('Explicit container role required')
    if Path.cwd().resolve()!=ROOT or Path(path).resolve()!=ROOT/'config/config.yaml':
        raise DeploymentError('Container root/configuration must be shared at /app')
    try:
        user=yaml.safe_load(Path(path).read_text())
    except (OSError,ValueError,yaml.YAMLError):
        raise DeploymentError('Container configuration unavailable or invalid') from None
    if not isinstance(user,dict):
        raise DeploymentError('Configuration must be a mapping')
    config=copy.deepcopy(DEFAULT_CONFIG)
    for key,value in user.items():
        if isinstance(value,dict) and isinstance(config.get(key),dict):config[key].update(value)
        else:config[key]=value
    if any(not isinstance(config.get(key),dict) for key in ('service','llm','paths','web','icecast')):
        raise DeploymentError('Container sections must be mappings')
    if (config['web'].get('host'),config['web'].get('port'))!=('0.0.0.0',5055):
        raise DeploymentError('Managed web bind and port required')
    if config.get('paths')!=PATHS:
        raise DeploymentError('Container storage paths cannot be overridden')
    llm=config.get('llm',{})
    if not isinstance(llm.get('cache',{}),dict):raise DeploymentError('LLM cache must be a mapping')
    if llm.get('lock_dir','data')!='data' or llm.get('lock_resource','local-llm')!='local-llm' or llm.get('cache',{}).get('dir','data/llm_cache')!='data/llm_cache':
        raise DeploymentError('Shared LLM resource identity required')
    if llm.get('provider') not in ('llama-server','ollama'):
        raise DeploymentError('Existing trusted LLM backend required')
    endpoint=llm.get('base_url','')
    if (endpoint and not trusted_url(endpoint)) or (role!='web' and (not endpoint or not llm.get('model'))):
        raise DeploymentError('Explicit trusted LLM URL required for workers')
    if llm['provider']=='ollama':
        if not endpoint:raise DeploymentError('Explicit trusted Ollama URL required')
        os.environ['OLLAMA_HOST']=endpoint
    ice=config.setdefault('icecast',{})
    if any(key in ice for key in ('password','admin_password','source_password')):
        raise DeploymentError('Icecast credentials belong in secret files')
    if (ice.get('host'),ice.get('port'),ice.get('user'),ice.get('mount'),ice.get('android_mount'))!=('icecast',8000,'source','/nova','/nova-android'):
        raise DeploymentError('Managed Icecast origin and mounts required')
    if role in ('web','radio'):
        password=read_secret('icecast_source_password')
        if len(password)!=64 or any(c not in '0123456789abcdef' for c in password):
            raise DeploymentError('Generated Icecast source secret required')
        ice['password']=password
    return config


def writable_paths(role):
    return ['data','logs','tmp']+(['config','site','audio_queue'] if role=='web' else ['site'] if role=='news' else ['audio_queue'])


def check_storage(role):
    import tempfile
    for relative in writable_paths(role):
        directory=ROOT/relative
        require_local_filesystem(directory)
        if not directory.is_dir():raise DeploymentError('Required writable storage missing')
        # Probe only a unique disposable file, never a live lock or application file.
        with tempfile.NamedTemporaryFile(dir=directory,prefix='.health-') as probe:
            probe.write(b'check');probe.flush();os.fsync(probe.fileno())


_ROLE_LEASES=[]

def acquire_role(role):
    import fcntl
    path=ROOT/'data'/('service-'+role+'.lock')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1:
            raise DeploymentError('Unsafe role lease')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        os.set_inheritable(fd,True)  # Keep singleton admission across exec and supervisor forks.
        _ROLE_LEASES.append(fd)
    except Exception:
        os.close(fd)
        raise DeploymentError('Role already active or lease unavailable') from None


def require_local_filesystem(path):
    """Refuse known remote/virtual file-sharing backends; local POSIX remains required."""
    try:
        rows=Path('/proc/self/mountinfo').read_text().splitlines()
    except OSError:
        raise DeploymentError('Linux mount information required') from None
    selected=None
    for row in rows:
        left,right=row.split(' - ',1)
        mount=Path(left.split()[4].replace('\\040',' '))
        if Path(path).resolve().is_relative_to(mount):
            if selected is None or len(str(mount))>selected[0]:selected=(len(str(mount)),right.split()[0])
    if selected is None or selected[1] in {'nfs','nfs4','cifs','smb3','ceph','9p','virtiofs'} or selected[1].startswith('fuse'):
        raise DeploymentError('Shared storage must be a local POSIX filesystem')
