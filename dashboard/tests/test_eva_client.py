import httpx
import pytest

import eva_client


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EVA_BASE_URL", "https://eva.test/")
    monkeypatch.setenv("ADMIN_SECRET", "s3cr3t")


def _mock(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(eva_client.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))


@pytest.mark.asyncio
async def test_sends_secret_and_returns_json(monkeypatch):
    seen = {}
    def handler(req):
        seen["url"] = str(req.url)
        seen["secret"] = req.headers.get("X-Admin-Secret")
        return httpx.Response(200, json={"ok": 1})
    _mock(monkeypatch, handler)
    status, body = await eva_client.post("/admin/panel/appointments", {"a": 1})
    assert (status, body) == (200, {"ok": 1})
    assert seen == {"url": "https://eva.test/admin/panel/appointments", "secret": "s3cr3t"}


@pytest.mark.asyncio
async def test_passes_409_through(monkeypatch):
    _mock(monkeypatch, lambda req: httpx.Response(409, json={"detail": {"needs_encaixe": True, "reasons": ["x"]}}))
    status, body = await eva_client.post("/x", {})
    assert status == 409 and body["detail"]["needs_encaixe"] is True


@pytest.mark.asyncio
async def test_network_error_raises_eva_unavailable(monkeypatch):
    def handler(req):
        raise httpx.ConnectError("down")
    _mock(monkeypatch, handler)
    with pytest.raises(eva_client.EvaUnavailable):
        await eva_client.post("/x", {})


@pytest.mark.asyncio
async def test_missing_config_raises(monkeypatch):
    monkeypatch.delenv("EVA_BASE_URL")
    with pytest.raises(eva_client.EvaUnavailable):
        await eva_client.post("/x", {})


@pytest.mark.asyncio
async def test_timeout_raises_eva_timeout(monkeypatch):
    def handler(req):
        raise httpx.ReadTimeout("timed out")
    _mock(monkeypatch, handler)
    with pytest.raises(eva_client.EvaTimeout):
        await eva_client.post("/x", {})


@pytest.mark.asyncio
async def test_eva_timeout_is_an_eva_unavailable(monkeypatch):
    """O chamador que só sabe tratar EvaUnavailable continua funcionando."""
    def handler(req):
        raise httpx.ReadTimeout("timed out")
    _mock(monkeypatch, handler)
    with pytest.raises(eva_client.EvaUnavailable):
        await eva_client.post("/x", {})


@pytest.mark.asyncio
async def test_post_forwards_timeout_to_httpx_client(monkeypatch):
    seen = {}
    real = eva_client.httpx.AsyncClient
    def fake_client(**kw):
        seen["timeout"] = kw.get("timeout")
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": 1}))
        return real(transport=transport, **kw)
    monkeypatch.setattr(eva_client.httpx, "AsyncClient", fake_client)
    await eva_client.post("/x", {}, timeout=60.0)
    assert seen["timeout"] == 60.0
