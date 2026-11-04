"""Cooperative WSGI lifecycle used by the existing multiprocessing supervisor."""
import threading
from werkzeug.serving import make_server, WSGIRequestHandler


class BoundedRequestHandler(WSGIRequestHandler):
    timeout=30


def serve(app,host,port,stop_event):
    app.extensions['atlas_stop_event']=stop_event
    @app.before_request
    def stopping():
        if stop_event.is_set():return {'status':'error','msg':'Service stopping'},503
    server=make_server(host,port,app,threaded=True,request_handler=BoundedRequestHandler)
    server.daemon_threads=False
    server.block_on_close=True
    def run_http():
        try:server.serve_forever()
        finally:stop_event.set()
    worker=threading.Thread(target=run_http,daemon=True,name='HTTPServer')
    worker.start()
    try:
        stop_event.wait()
    finally:
        server.shutdown()
        worker.join()
        server.server_close()  # Drain accepted requests; stream generators observe the stop event.
        for job in app.extensions.get('atlas_background_jobs',[]):
            job.join()  # Keep JSON/MP3 publication cooperative, including manual flash jobs.
