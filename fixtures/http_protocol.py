"""Tiny HTTP handler fixture, usable with in-memory transport or a local server."""
from http.server import BaseHTTPRequestHandler
import json


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        method = request['method']
        payload = {'jsonrpc': '2.0', 'id': request.get('id')}
        if method == 'tools/call':
            params = request['params']; args = params['arguments']; slug = args['slug']
            page = self.server.pages.get(slug)
            error = False
            if params['name'] == 'get_page':
                value = page or {}
            elif page and args.get('expected_revision') != page['revision']:
                error = True; value = {'code': 'revision_conflict'}
            else:
                value = {'revision': str(int(page['revision']) + 1) if page else '1',
                         'content': args['content'], 'written': False}
                self.server.pages[slug] = value
            payload['result'] = {'isError': error, 'content': [{'type': 'text', 'text': json.dumps(value)}]}
        else:
            payload['result'] = {}
        encoded = json.dumps(payload).encode()
        if method == 'notifications/initialized':
            encoded = b''
        elif self.server.sse:
            encoded = b'data: ' + encoded + b'\n\n'
        self.send_response(200)
        self.send_header('Content-Length', str(len(encoded)))
        self.send_header('mcp-session-id', 'fixture-session')
        self.end_headers()
        self.wfile.write(encoded)
