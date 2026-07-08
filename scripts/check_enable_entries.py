import json
import urllib.request
import urllib.error

urls = [
    'http://127.0.0.1:8000/runtime/preflight',
    'http://127.0.0.1:8000/execution/fills/status',
    'http://127.0.0.1:8000/risk/entries',
]
for url in urls:
    print('---', url)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            print(r.status)
            print(json.dumps(json.load(r), indent=2))
    except urllib.error.HTTPError as e:
        print('HTTP', e.code)
        try:
            print(e.read().decode())
        except Exception:
            pass
    except Exception as e:
        print('ERROR', e)
print('--- POST enable entries')
req = urllib.request.Request(
    'http://127.0.0.1:8000/risk/entries/enable',
    method='POST',
    data=b'{}',
    headers={'Content-Type': 'application/json'},
)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(r.status)
        print(json.dumps(json.load(r), indent=2))
except urllib.error.HTTPError as e:
    print('HTTP', e.code)
    try:
        print(e.read().decode())
    except Exception:
        pass
except Exception as e:
    print('ERROR', e)
