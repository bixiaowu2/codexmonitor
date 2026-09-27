import json, math, urllib.request, urllib.error

class FetchError(RuntimeError):
    def __init__(self, code, retry_after=None):
        self.code = code
        try:
            self.retry_after = max(0, min(int(float(retry_after)), 3600)) if retry_after is not None else None
        except (TypeError, ValueError):
            self.retry_after = None
        super().__init__(code)

def request_json(url, timeout=10, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers={'User-Agent':'meme-radar/0.2','Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(3_000_000))
    except urllib.error.HTTPError as e:
        retry_after = e.headers.get('Retry-After')
        try:
            retry_after = float(retry_after) if retry_after is not None else None
            if retry_after is not None and not math.isfinite(retry_after): retry_after = None
        except (TypeError, ValueError):
            retry_after = None
        raise FetchError('http_'+str(e.code), retry_after) from None
    except Exception as e:
        raise FetchError(type(e).__name__) from None

def get_json(url, timeout=10): return request_json(url, timeout)
def post_json(url, payload, timeout=10): return request_json(url, timeout, payload)
