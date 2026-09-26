import json, urllib.request, urllib.error

class FetchError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def request_json(url, timeout=10, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers={'User-Agent':'meme-radar/0.2','Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(3_000_000))
    except urllib.error.HTTPError as e:
        raise FetchError('http_'+str(e.code)) from None
    except Exception as e:
        raise FetchError(type(e).__name__) from None

def get_json(url, timeout=10): return request_json(url, timeout)
def post_json(url, payload, timeout=10): return request_json(url, timeout, payload)
