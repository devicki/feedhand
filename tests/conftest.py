import pytest


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDHAND_HOME", str(tmp_path / "home"))
    for k in ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET", "THREADS_ACCESS_TOKEN",
              "THREADS_USER_ID", "IG_ACCESS_TOKEN", "IG_USER_ID"]:
        monkeypatch.delenv(k, raising=False)
    yield


class Resp:
    def __init__(self, code=200, data=None):
        self.status_code = code
        self._d = data or {}
        self.text = str(self._d)
        self.content = b"x" if data is not None else b""

    def json(self):
        return self._d


class FakeSession:
    """Routes (method, url-suffix) -> list of responses (popped) or one response."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, suf), r in self.routes.items():
            if m == method and url.endswith(suf):
                if isinstance(r, list):
                    return r.pop(0) if len(r) > 1 else r[0]
                return r
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)
