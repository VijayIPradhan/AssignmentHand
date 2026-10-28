import pytest

from app.storage import LocalStorage, StorageError, safe_filename
from tests.conftest import run_jobs
from tests.pdfs import assignment_pdf


def test_auth_required(client):
    for path in ("/api/v1/dashboard", "/api/v1/profiles", "/api/v1/assignments", "/api/v1/auth/me"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_register_login_logout_and_bad_password(client):
    r = client.post("/api/v1/auth/register", json={"email": "A@Example.com", "password": "longenough1"})
    assert r.status_code == 201
    assert (
        client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "longenough1"}).status_code
        == 409
    )
    client.post("/api/v1/auth/logout")
    assert (
        client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong-password"}).status_code
        == 401
    )
    r = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "longenough1"})
    assert r.status_code == 200 and "ah_session" in r.cookies
    assert client.get("/api/v1/auth/me").json()["email"] == "a@example.com"
    token = r.json()["access_token"]
    client.cookies.clear()
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_users_cannot_see_each_others_data(client):
    client.post("/api/v1/auth/register", json={"email": "alice@example.com", "password": "alice-password"})
    aid = client.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())}).json()["id"]
    pid = client.post("/api/v1/profiles", json={"name": "alice"}).json()["id"]
    run_jobs()
    qid = client.get(f"/api/v1/assignments/{aid}/questions").json()[0]["id"]
    doc = client.get(f"/api/v1/assignments/{aid}").json()["documents"][0]["id"]

    client.cookies.clear()
    client.post("/api/v1/auth/register", json={"email": "mallory@example.com", "password": "mallory-password"})
    for method, path in [
        ("get", f"/api/v1/assignments/{aid}"),
        ("delete", f"/api/v1/assignments/{aid}"),
        ("get", f"/api/v1/profiles/{pid}"),
        ("patch", f"/api/v1/questions/{qid}"),
        ("get", f"/api/v1/assignments/{aid}/documents/{doc}/pages/1.png"),
        ("get", f"/api/v1/assignments/{aid}/documents/{doc}/source.pdf"),
    ]:
        r = getattr(client, method)(path, **({"json": {"text": "x"}} if method == "patch" else {}))
        assert r.status_code == 404, (method, path)
    assert client.get("/api/v1/assignments").json() == []
    # cannot attach someone else's profile
    mine = client.post("/api/v1/assignments", files={"file": ("m.pdf", assignment_pdf())}).json()["id"]
    assert client.patch(f"/api/v1/assignments/{mine}", json={"profile_id": pid}).status_code == 404


def test_figure_endpoint_rejects_foreign_keys(auth_client):
    aid = auth_client.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())}).json()["id"]
    r = auth_client.get(f"/api/v1/assignments/{aid}/figures", params={"key": "users/other/assignments/x/figures/y.png"})
    assert r.status_code == 404


def test_storage_blocks_path_traversal(tmp_path):
    st = LocalStorage(tmp_path / "root")
    for bad in ("../escape.txt", "a/../../b", "/etc/passwd", "a//b", "..", "C:\\x"):
        with pytest.raises(StorageError):
            st.put(bad, b"x")
    st.put("users/u/ok.txt", b"fine")
    assert st.get("users/u/ok.txt") == b"fine"


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\Users\\me\\My <Essay>.pdf") == "My _Essay_.pdf"
    assert safe_filename("") == "file"


def test_rate_limit_on_expensive_endpoints(auth_client, monkeypatch, fake_llm):
    from app import config

    monkeypatch.setenv("RATE_LIMIT_GENERATE_PER_MIN", "2")
    config.get_settings.cache_clear()
    aid = auth_client.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())}).json()["id"]
    run_jobs()
    auth_client.post(f"/api/v1/assignments/{aid}/confirm-extraction")
    codes = [auth_client.post(f"/api/v1/assignments/{aid}/generate", json={}).status_code for _ in range(3)]
    assert codes[:2] == [202, 202] and codes[2] == 429


def test_validation_errors_are_structured(auth_client):
    r = auth_client.post("/api/v1/profiles", json={"name": ""})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["details"][0]["loc"][-1] == "name"


def test_account_deletion_removes_all_files(auth_client, tmp_env):
    auth_client.post("/api/v1/assignments", files={"file": ("a.pdf", assignment_pdf())})
    run_jobs()
    from tests.conftest import stored_keys

    assert any(k.startswith("users/") and k.endswith(".pdf") for k in stored_keys())
    assert auth_client.delete("/api/v1/auth/me").status_code == 204
    assert not any(k.startswith("users/") for k in stored_keys())


def test_health_endpoints(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    r = client.get("/api/v1/health/ready").json()
    assert r["status"] == "ok" and r["checks"]["database"] == "ok" and r["checks"]["storage"] == "ok"
