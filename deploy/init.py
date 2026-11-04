"""Initialize a NEW local deployment directory; never import existing user data."""
import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import sys
import xml.etree.ElementTree as ET
import yaml
from werkzeug.security import generate_password_hash
from deploy.runtime import PATHS,trusted_url


def private_write(path,text):
    with path.open('x',encoding='utf-8') as out:
        os.chmod(path,0o400)
        out.write(text)


def initialize(root,username,password,*,llm_url='',provider='llama-server',model='',icecast_admin='atlas-admin'):
    root=Path(root).absolute()
    if root.exists() or root.is_symlink():raise ValueError('Initialization requires a new directory')
    checkout=Path(__file__).resolve().parents[1]
    if root.is_relative_to(checkout) and root != checkout/'deploy/local':
        raise ValueError('Use the ignored default directory or a path outside the checkout')
    if any(c in str(root) for c in '\n\r#$ \'"'):raise ValueError('Use a simple deployment path')
    if not username or len(password)<16 or not re.fullmatch(r'[A-Za-z0-9_-]+',icecast_admin):raise ValueError('Explicit strong administrator credentials required')
    if provider not in ('llama-server','ollama') or (llm_url and not trusted_url(llm_url)):raise ValueError('Invalid trusted LLM configuration')
    if os.getuid()==0:raise ValueError('Initialize as the intended non-root service owner')
    previous=os.umask(0o077)
    try:
        root.mkdir(parents=True,mode=0o700)
        for directory in ('config','data/articles','data/reports','data/editions','data/posts','data/llm_cache','site','audio_queue/android_cache','music','background_music','logs/web','logs/news','logs/radio','secrets'):
            (root/directory).mkdir(parents=True,exist_ok=True,mode=0o700)
        source=secrets.token_hex(32)
        for name,value in {'admin_username':username,'admin_password_hash':generate_password_hash(password,method='scrypt:32768:8:1'),
                           'session_secret':secrets.token_urlsafe(48),'icecast_source_password':source}.items():
            private_write(root/'secrets'/name,value+'\n')
        xml=ET.Element('icecast')
        def element(parent,name,text):ET.SubElement(parent,name).text=str(text)
        element(xml,'location','Private Nova Atlas');element(xml,'admin','operator@example.invalid')
        limits=ET.SubElement(xml,'limits');element(limits,'clients',100);element(limits,'sources',2);element(limits,'source-timeout',10)
        auth=ET.SubElement(xml,'authentication')
        for key,value in {'source-password':source,'relay-password':secrets.token_hex(32),'admin-user':icecast_admin,'admin-password':secrets.token_hex(32)}.items():element(auth,key,value)
        element(xml,'hostname','icecast')
        socket=ET.SubElement(xml,'listen-socket');element(socket,'port',8000);element(socket,'bind-address','0.0.0.0')
        paths=ET.SubElement(xml,'paths');element(paths,'basedir','/usr/share/icecast');element(paths,'logdir','/tmp');element(paths,'webroot','/usr/share/icecast/web');element(paths,'adminroot','/usr/share/icecast/admin')
        logging=ET.SubElement(xml,'logging');element(logging,'accesslog','/dev/null');element(logging,'errorlog','-');element(logging,'loglevel',2)
        for name in ('/nova','/nova-android'):
            mount=ET.SubElement(xml,'mount',{'type':'normal'});element(mount,'mount-name',name);element(mount,'public',0)
        private_write(root/'secrets/icecast.xml',ET.tostring(xml,encoding='unicode')+'\n')
        config={'service':{'name':'Nova Atlas','tagline':'Private installation','default_language':'fr'},
                'llm':{'provider':provider,'base_url':llm_url,'model':model,'lock_dir':'data','lock_resource':'local-llm','cache':{'enabled':True,'dir':'data/llm_cache','ttl_days':7}},
                'icecast':{'host':'icecast','port':8000,'user':'source','mount':'/nova','android_mount':'/nova-android'},
                'web':{'host':'0.0.0.0','port':5055},'paths':dict(PATHS)}
        (root/'config/config.yaml').write_text(yaml.safe_dump(config,allow_unicode=True))
        (root/'config/feeds.yaml').write_text('{}\n') # No sources enabled implicitly.
        defaults=Path(__file__).resolve().parents[1]/'config'
        for name in ('bulletins.yaml','messages.yaml'):
            (root/'config'/name).write_bytes((defaults/name).read_bytes())
        env=(f'ATLAS_STORAGE={root}\nATLAS_UID={os.getuid()}\nATLAS_GID={os.getgid()}\n'
             'ATLAS_WEB_PORT=5055\nATLAS_COOKIE_SECURE=1\nATLAS_TRUSTED_PROXIES=\nATLAS_HSTS_MAX_AGE=0\nATLAS_STOP_GRACE=45m\nATLAS_TIMEZONE=Europe/Paris\n')
        (root/'compose.env').write_text(env)
    finally:os.umask(previous)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',default='deploy/local');parser.add_argument('--admin-username',required=True)
    parser.add_argument('--password-stdin',action='store_true');parser.add_argument('--llm-url',default='')
    parser.add_argument('--provider',choices=['llama-server','ollama'],default='llama-server');parser.add_argument('--model',default='')
    args=parser.parse_args()
    password=sys.stdin.readline().rstrip('\n') if args.password_stdin else getpass.getpass('Administrator password (16+ characters): ')
    if not args.password_stdin and password!=getpass.getpass('Confirm administrator password: '):raise SystemExit('Passwords do not match')
    try:initialize(args.directory,args.admin_username,password,llm_url=args.llm_url,provider=args.provider,model=args.model)
    except (ValueError,OSError):raise SystemExit('Initialization refused; use a new simple path, non-root owner, explicit identity and trusted endpoint.') from None
    print('Private deployment files initialized. No service started.')


if __name__=='__main__':main()
