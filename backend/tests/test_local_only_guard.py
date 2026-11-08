"""Whole-API local-only guard (real app): foreign Host / Origin are refused, loopback works, test clients are exempt."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

LOCAL = "http://127.0.0.1:8000"
GEN = dict(genre="Fantasy", object_name="Wand", category=1, provider="mock", model="mock-placeholder-v1")


@pytest.fixture
def guarded(settings):
    with TestClient(create_app(settings, enforce_local=True), base_url=LOCAL) as c:
        yield c


def test_loopback_host_works_for_reads_and_writes(guarded):
    assert guarded.get("/api/health").status_code == 200
    assert guarded.post("/api/generate-card", json=GEN).status_code == 200
    for host in ("localhost:8000", "[::1]:8000", "127.0.0.1"):
        assert guarded.get("/api/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", "testserver", "10.0.0.5:8000", ""])
def test_foreign_host_is_refused_everywhere_including_paid_endpoints(guarded, host):
    for method, path in (("get", "/api/health"), ("get", "/api/models"), ("post", "/api/generate-card"), ("post", "/api/jobs")):
        r = getattr(guarded, method)(path, headers={"Host": host}, **({"json": GEN} if method == "post" else {}))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden_host", (host, path)


def test_cross_site_origin_cannot_mutate_but_loopback_dev_origin_can(guarded):
    r = guarded.post("/api/generate-card", json=GEN, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden_origin"
    r = guarded.post("/api/references", files={"file": ("a.png", b"x", "image/png")}, data={"ref_type": "style"},
                     headers={"Origin": "http://evil.example:8000"})  # the "simple" multipart CSRF shape
    assert r.status_code == 403
    assert guarded.post("/api/generate-card", json=GEN, headers={"Origin": "http://localhost:5173"}).status_code == 200
    assert guarded.post("/api/generate-card", json=GEN, headers={"Origin": "http://127.0.0.1:5199"}).status_code == 200
    # reads with a foreign Origin are not mutations (the browser's CORS rules keep the response unreadable)
    assert guarded.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 200


def test_foreign_origin_requests_leave_no_side_effects(guarded):
    before = len(guarded.get("/api/artifacts").json())
    guarded.post("/api/generate-card", json=GEN, headers={"Origin": "https://evil.example"})
    assert len(guarded.get("/api/artifacts").json()) == before


def test_default_test_app_is_not_guarded(settings):
    with TestClient(create_app(settings)) as c:  # base_url http://testserver
        assert c.get("/api/health").status_code == 200
