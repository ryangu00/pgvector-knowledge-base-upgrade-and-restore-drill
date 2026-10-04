"""Five-commit loose-object Git repository, built without invoking a Git command.

The query adapter reads genuine blobs, trees and commits but implements only the
queries used by the pre-screen. It is not evidence about the Git CLI or packfiles.
"""
import difflib
import hashlib
from pathlib import Path
import re
import zlib


class ToyGit:
    def __init__(self, root):
        self.root = Path(root)
        self.git = self.root / '.git'
        (self.git / 'objects').mkdir(parents=True)
        (self.git / 'refs/heads').mkdir(parents=True)
        self.refs = {}
        base = {'overlap.py': 'def stable():\n    return "base"\n',
                'clean.py': 'def untouched():\n    pass\n',
                'deleted.py': 'def removed():\n    pass\n'}
        self.refs['base'] = self.commit(base, None, 'base')
        upstream = dict(base)
        upstream['overlap.py'] = 'def stable():\n    return "upstream"\n'
        upstream.pop('deleted.py')
        upstream['collision.py'] = 'def upstream_name():\n    pass\n'
        self.refs['target'] = self.commit(upstream, self.refs['base'], 'upstream')
        parent = self.refs['base']; local = dict(base)
        for file, body in [
            ('overlap.py', 'def stable():\n    return "local"\n'),
            ('new.py', 'class LocalOnly:\n    pass\n'),
            ('clean.py', 'def untouched():\n    return "local"\n'),
            ('deleted.py', 'def removed():\n    return "local"\n'),
            ('collision.py', 'function collision() {}\n')]:
            local[file] = body
            parent = self.commit(local, parent, 'change ' + file)
        self.refs['local'] = parent
        for name, oid in self.refs.items():
            (self.git / 'refs/heads' / name).write_text(oid + '\n')
        (self.git / 'HEAD').write_text('ref: refs/heads/local\n')

    def put(self, kind, body):
        data = kind.encode() + b' ' + str(len(body)).encode() + b'\0' + body
        oid = hashlib.sha1(data).hexdigest()
        file = self.git / 'objects' / oid[:2] / oid[2:]
        file.parent.mkdir(exist_ok=True)
        file.write_bytes(zlib.compress(data))
        return oid

    def read(self, ref):
        oid = self.refs.get(ref, ref)
        raw = zlib.decompress((self.git / 'objects' / oid[:2] / oid[2:]).read_bytes())
        header, body = raw.split(b'\0', 1)
        assert int(header.split()[1]) == len(body)
        return body

    def commit(self, files, parent, subject):
        tree = b''
        for file, body in sorted(files.items()):
            oid = self.put('blob', body.encode())
            tree += b'100644 ' + file.encode() + b'\0' + bytes.fromhex(oid)
        treeid = self.put('tree', tree)
        body = 'tree ' + treeid + '\n'
        if parent:
            body += 'parent ' + parent + '\n'
        body += 'author Fixture <fixture@example.invalid> 1 +0000\n'
        body += 'committer Fixture <fixture@example.invalid> 1 +0000\n\n' + subject + '\n'
        return self.put('commit', body.encode())

    def files(self, ref):
        treeid = self.read(ref).decode().splitlines()[0].split()[1]
        tree = self.read(treeid); files = {}
        while tree:
            head, tail = tree.split(b'\0', 1)
            oid, tree = tail[:20].hex(), tail[20:]
            files[head.split(b' ', 1)[1].decode()] = self.read(oid).decode()
        return files

    def parent(self, ref):
        return next(line.split()[1] for line in self.read(ref).decode().splitlines() if line.startswith('parent '))

    def __call__(self, *args, missing=False):
        command = args[0]
        if command == 'merge-base': return self.refs['base']
        if command == 'rev-list':
            current = self.refs['local']; commits = []
            while current != self.refs['base']:
                commits.append(current); current = self.parent(current)
            return '\n'.join(reversed(commits))
        if command == 'ls-tree':
            return args[-1] if args[-1] in self.files(args[2]) else ''
        if command == 'log':
            file = args[-1]
            return self.refs['target'] if self.files('base').get(file) != self.files('target').get(file) else ''
        if command == 'diff-tree':
            ref = args[-1]; a, b = self.files(self.parent(ref)), self.files(ref)
            return '\n'.join(sorted(f for f in set(a) | set(b) if a.get(f) != b.get(f)))
        if command == 'show':
            if '--format=%cI' in args: return '2026-09-04T00:00:00+00:00'
            if '--format=%s' in args: return self.read(args[-1]).decode().split('\n\n')[1].strip()
            ref, file = args[3], args[-1]
            a, b = self.files(self.parent(ref)).get(file, ''), self.files(ref).get(file, '')
            return '\n'.join(difflib.unified_diff(a.splitlines(), b.splitlines(), n=0))
        if command == 'grep':
            regex = args[3].replace('[[:space:]]', r'\s').replace('[^[:alnum:]_]', r'[^A-Za-z0-9_]')
            return '\n'.join(f + ':' + line for f, body in self.files(args[4]).items()
                             for line in body.splitlines() if re.search(regex, line))
        raise ValueError('unsupported fixture query')
