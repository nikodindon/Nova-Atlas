"""CI only: local fake mount + real FFmpeg samples; not a physical or Icecast trial."""
import json,os,shutil,subprocess,tempfile,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from deploy.field_radio import bounded,collector
from deploy.field_metrics import run
from deploy.field_report import load


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('Disposable CI only')
    if not shutil.which('ffmpeg'):raise SystemExit('FFmpeg required for field probe smoke')
    with tempfile.TemporaryDirectory(prefix='atlas-field-probe-') as tmp:
        root=Path(tmp);audio=root/'tone.mp3'
        subprocess.run(['ffmpeg','-v','error','-nostdin','-f','lavfi','-i','sine=frequency=440:duration=2','-c:a','libmp3lame','-b:a','128k',str(audio)],check=True,timeout=10)
        raw=audio.read_bytes()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/rate':
                    self.send_response(429);self.send_header('Retry-After','300');self.end_headers();return
                if self.path not in ('/primary','/android'):
                    self.send_response(503);self.end_headers();return
                self.send_response(200);self.send_header('Content-Type','audio/mpeg');self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            values=collector({'primary':base+'/primary','android':base+'/android'},loopback_http=True)
            report=root/'observations.jsonl';run(values,report)
            summary=load(report)
            assert summary['samples']==1 and summary['radio_24h']=='NOT_TESTED'
            assert all(r['decode_pass_samples']==1 and r['bytes_received']>=8192 for r in summary['channels'].values())
            limited=bounded(base+'/rate',True);assert limited['retry_after_seconds']==300 and limited['mount']=='FAIL'
        finally:server.shutdown();server.server_close();thread.join(timeout=5)
        assert not thread.is_alive()
        assert bounded(base+'/primary',True)['mount']=='FAIL'
        print('Field probe smoke PASS: synthetic local HTTP samples decoded, 429 respected, stopped receiver observed; no Icecast/phone/endurance qualification')
if __name__=='__main__':main()
