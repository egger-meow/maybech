import urllib.request, json
req=urllib.request.Request('http://127.0.0.1:8000/risk/entries/enable', data=json.dumps({'confirm':True}).encode(), headers={'Content-Type':'application/json'})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print('STATUS', r.status)
        print(json.dumps(json.load(r), indent=2))
except Exception as e:
    import traceback
    traceback.print_exc()
