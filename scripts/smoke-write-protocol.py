#!/usr/bin/env python3
"""HTTP JSON-RPC create/reject/read/update smoke, with independent read-back."""
import argparse
import json
import os
import urllib.request


class Client:
    def __init__(self, endpoint, token='', opener=None):
        self.endpoint, self.token = endpoint, token
        self.session = None
        self.sequence = 0
        self.opener = opener or urllib.request.urlopen

    def call(self, method, params=None, notify=False):
        self.sequence += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if not notify:
            body['id'] = self.sequence
        if params is not None:
            body['params'] = params
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self.session:
            headers['mcp-session-id'] = self.session
        request = urllib.request.Request(self.endpoint, data=json.dumps(body).encode(), headers=headers)
        with self.opener(request, timeout=60) as response:
            self.session = response.headers.get('mcp-session-id') or self.session
            raw = response.read().decode()
        if notify or not raw.strip():
            return None
        if raw.lstrip().startswith('{'):
            return json.loads(raw)
        for line in raw.splitlines():
            if line.startswith('data:'):
                value = json.loads(line[5:].strip())
                if value.get('id') == self.sequence:
                    return value
        raise ValueError('no matching JSON-RPC response')

    def tool(self, name, arguments):
        response = self.call('tools/call', {'name': name, 'arguments': arguments})
        if response.get('error'):
            return False, response['error']
        result = response['result']
        text = ''.join(c.get('text', '') for c in result.get('content', []))
        return not result.get('isError', False), json.loads(text or '{}')


def smoke(client, slug, put='put_page', get='get_page'):
    client.call('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                              'clientInfo': {'name': 'write-smoke', 'version': '1'}})
    client.call('notifications/initialized', notify=True)
    ok, receipt = client.tool(put, {'slug': slug, 'content': 'initial synthetic body'})
    assert ok and receipt.get('revision'), 'new write not committed'
    ok, error = client.tool(put, {'slug': slug, 'content': 'unversioned update'})
    assert not ok and error.get('code') == 'revision_conflict', 'wrong rejection'
    ok, page = client.tool(get, {'slug': slug, 'include_content': True})
    assert ok and page.get('revision'), 'no revision on read'
    assert page.get('content') == 'initial synthetic body', 'rejected update changed body'
    ok, receipt = client.tool(put, {'slug': slug, 'content': 'versioned synthetic body',
                                   'expected_revision': page['revision']})
    assert ok and receipt.get('revision'), 'versioned write failed'
    ok, page = client.tool(get, {'slug': slug, 'include_content': True})
    assert ok and page.get('content') == 'versioned synthetic body', 'read-back failed'
    print('write protocol smoke OK')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--endpoint', required=True)
    p.add_argument('--slug', required=True, help='use a fresh synthetic slug')
    p.add_argument('--put-tool', default='put_page')
    p.add_argument('--get-tool', default='get_page')
    a = p.parse_args()
    smoke(Client(a.endpoint, os.environ.get('SMOKE_TOKEN', '')), a.slug, a.put_tool, a.get_tool)
