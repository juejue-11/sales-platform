"""A slow occupied port must not replace the running service record."""
import hashlib
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SlowHealth(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(4)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true,"app":"sales-screening"}')
        except (BrokenPipeError, ConnectionError):
            pass

    def log_message(self, *args):
        pass


def main():
    record = ROOT / 'data/service.json'
    before = hashlib.sha256(record.read_bytes()).hexdigest()
    server = ThreadingHTTPServer(('127.0.0.1', 8989), SlowHealth)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        powershell = Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(ROOT / '启动平台.ps1'), '-Port', '8989'],
                                cwd=ROOT, capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode != 0, 'A second service was started on a busy port'
        assert hashlib.sha256(record.read_bytes()).hexdigest() == before, 'Live service record was overwritten'
        print(json.dumps({'busy_port': 'passed', 'live_service_record_preserved': True}))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
