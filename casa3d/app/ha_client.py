"""Home Assistant API access through the app's existing Supervisor identity."""
import json
import os
from urllib.request import Request, urlopen
from pathlib import Path


def supervisor_token(environ=None, env_dir=Path('/run/s6/container_environment')):
    environment = os.environ if environ is None else environ
    token = environment.get('SUPERVISOR_TOKEN')
    if token:
        return token
    try:
        return (env_dir / 'SUPERVISOR_TOKEN').read_text().strip()
    except OSError:
        return ''


class HAClient:
    def __init__(self):
        self.token = supervisor_token()
        if not self.token:
            raise RuntimeError('Acesso ao Home Assistant indisponivel')
        self.socket = None
        self.sequence = 0

    def __enter__(self):
        import websocket
        self.socket = websocket.create_connection('ws://supervisor/core/websocket', timeout=30)
        json.loads(self.socket.recv())
        self.socket.send(json.dumps({'type': 'auth', 'access_token': self.token}))
        if json.loads(self.socket.recv()).get('type') != 'auth_ok':
            self.socket.close()
            raise RuntimeError('Acesso a API do Home Assistant nao autorizado')
        return self

    def __exit__(self, *args):
        self.socket.close()

    def call(self, command, **kwargs):
        # Core waits for service completion; cloud integration reloads can take minutes.
        self.socket.settimeout(180 if command == 'call_service' else 30)
        self.sequence += 1
        self.socket.send(json.dumps({'id': self.sequence, 'type': command, **kwargs}))
        while True:
            response = json.loads(self.socket.recv())
            if response.get('id') != self.sequence:
                continue
            if not response.get('success'):
                raise RuntimeError('Comando HA recusado: ' + str(response.get('error', {}).get('code', 'erro')))
            return response.get('result')

    def history(self, start, entities):
        from urllib.parse import quote, urlencode
        path = 'http://supervisor/core/api/history/period/' + quote(start, safe='')
        path += '?' + urlencode({'filter_entity_id': ','.join(entities), 'minimal_response': 'true'})
        request = Request(path, headers={'Authorization': 'Bearer ' + self.token})
        with urlopen(request, timeout=60) as response:
            return json.load(response)
