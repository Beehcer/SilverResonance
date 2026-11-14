"""Publish only reviewed public browser assets. Never uploads templates or state."""
import argparse
import configparser
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_server_bundle import ASSET_FILES
from workspace_runtime import adapt_workspace_js, adapt_workspace_chart_js


def payloads():
    files = {Path(p).name: (ROOT / p).read_bytes() for p in ASSET_FILES
             if p.startswith('static/') and Path(p).suffix in ('.js', '.css')}
    for target, source, adapter in (
        ('workspace-dual-runtime.js', 'workspace.js', adapt_workspace_js),
        ('workspace-dual-chart.js', 'workspace-chart.js', adapt_workspace_chart_js),
    ):
        files[target] = adapter(files[source].decode().replace('\r\n', '\n')).encode()
    return files


def publish(config):
    import oss2
    credentials = configparser.ConfigParser()
    credentials.read(config, encoding='utf-8')
    values = credentials['Credentials']
    auth = oss2.Auth(values['accessKeyId'], values['accessKeySecret'])
    endpoint = 'https://oss-cn-hangzhou.aliyuncs.com'
    bucket = oss2.Bucket(auth, endpoint, 'silver-resonance', connect_timeout=15)
    files = payloads()
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}
    version = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:24]
    base = f'https://silver-resonance.oss-cn-hangzhou.aliyuncs.com/workspace/{version}/'

    def upload(item):
        name, raw = item
        key = f'workspace/{version}/{name}'
        compressed = gzip.compress(raw, mtime=0)
        headers = {'Content-Type': 'text/css; charset=utf-8' if name.endswith('.css') else 'application/javascript; charset=utf-8',
                   'Content-Encoding': 'gzip', 'Cache-Control': 'public,max-age=31536000,immutable',
                   'x-oss-object-acl': 'public-read', 'x-oss-meta-sha256': hashes[name],
                   'x-oss-forbid-overwrite': 'true'}
        try:
            bucket.put_object(key, compressed, headers=headers)
        except oss2.exceptions.OssError as error:
            if error.code != 'FileAlreadyExists':
                raise
        # Verify unauthenticated access before allowing the entry page to use it.
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with client.open(base + name, timeout=20) as response:
            body = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            if hashlib.sha256(body).hexdigest() != hashes[name]:
                raise RuntimeError('Public object hash mismatch: ' + name)
        return len(raw), len(compressed)

    with ThreadPoolExecutor(max_workers=4) as pool:
        sizes = list(pool.map(upload, files.items()))
    result = {'version': version, 'assets': {'/static/'+name: base+name for name in sorted(files)}, 'sha256': hashes}
    path = ROOT / 'static/workspace-assets.json'
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)
    print(json.dumps({'version':version, 'files':len(files), 'raw_bytes':sum(x[0] for x in sizes), 'gzip_bytes':sum(x[1] for x in sizes)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path.home()/'.ossutilconfig')
    args = parser.parse_args()
    try:
        publish(args.config)
    except Exception as error:
        # SDK exceptions can contain request/auth details; expose only safe fields.
        print(json.dumps({'failed':True,'type':type(error).__name__,'code':getattr(error,'code',None),'status':getattr(error,'status',None)}))
        sys.exit(1)
