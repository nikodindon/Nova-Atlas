"""CI-only synthetic provider; not in production image context."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'{}')
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['model']=='synthetic-model'
        response={'id':'fake','object':'chat.completion','created':0,'model':'synthetic-model',
          'choices':[{'index':0,'message':{'role':'assistant','content':'Réponse synthétique française 🌍'},'finish_reason':'stop'}],
          'usage':{'prompt_tokens':5,'completion_tokens':5,'total_tokens':10}}
        raw=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
