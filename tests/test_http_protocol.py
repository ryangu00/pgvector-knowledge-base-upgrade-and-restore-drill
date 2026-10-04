import http.client
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_offline import module
from http_protocol import Handler


class Wire:
    def __init__(self, data):
        self.incoming = io.BytesIO(data)
        self.outgoing = io.BytesIO()

    def makefile(self, *args, **kwargs):
        return self.incoming

    def sendall(self, data):
        self.outgoing.write(data)


class HttpProtocolTests(unittest.TestCase):
    def test_plain_json_and_sse_over_in_memory_http_handler(self):
        m = module('smoke-write-protocol')
        for sse in (False, True):
            server = SimpleNamespace(pages={}, sse=sse)
            headers_seen = []
            def opener(request, timeout):
                headers_seen.append(dict(request.header_items()))
                headers = dict(request.header_items())
                headers['Content-Length'] = str(len(request.data))
                raw = ('POST / HTTP/1.1\r\n' + ''.join(k + ': ' + v + '\r\n'
                       for k, v in headers.items()) + '\r\n').encode() + request.data
                wire = Wire(raw)
                Handler(wire, ('127.0.0.1', 0), server)
                response = http.client.HTTPResponse(Wire(wire.outgoing.getvalue()))
                response.begin()
                return response
            with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
                with patch('builtins.print'):
                    m.smoke(m.Client('http://127.0.0.1/', opener=opener), 'fixture/smoke')
            self.assertEqual(server.pages['fixture/smoke']['content'], 'versioned synthetic body')
            self.assertTrue(any('Mcp-session-id' in h for h in headers_seen))


if __name__ == '__main__':
    unittest.main()
