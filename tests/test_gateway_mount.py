from __future__ import annotations

import json

import pytest
import requests as http
from flask import Flask, Response, request

from common.config import parse_config
from common.storage import atomic_write_json
from gateway.app import create_app

from tests.test_gateway import ThreadedServer, make_backend


def make_redirect_backend(name: str) -> Flask:
    """Backend that answers JSON, serves redirects and a PRG form flow."""
    app = Flask(f"backend-{name}")

    @app.get("/")
    def root():
        payload = {"service": name, "path": "/",
                   "query": request.args.to_dict(flat=False)}
        return Response(json.dumps(payload), mimetype="application/json")

    @app.post("/")
    def root_submit():
        # PRG: the action-less form posts to the mount entry (upstream "/").
        response = Response(status=302)
        response.headers["Location"] = (
            "result?submitted=" + request.form.get("name", "") + "#done"
        )
        return response

    @app.get("/<path:path>")
    @app.post("/<path:path>")
    def echo(path: str = ""):
        payload = {"service": name, "path": "/" + path,
                   "query": request.args.to_dict(flat=False)}
        response = Response(json.dumps(payload), mimetype="application/json")
        response.headers["X-Sample-Service"] = name
        return response

    @app.get("/redirect/relative")
    def redirect_relative():
        response = Response(status=302)
        response.headers["Location"] = "../result?came=relative#frag"
        return response

    @app.get("/redirect/absolute")
    def redirect_absolute():
        response = Response(status=302)
        response.headers["Location"] = (
            f"http://{request.host}/result?came=absolute#abs"
        )
        return response

    @app.get("/redirect/offsite")
    def redirect_offsite():
        response = Response(status=302)
        response.headers["Location"] = "https://example.com/ext?keep=1#ext"
        return response

    return app


@pytest.fixture()
def mounted_gateway(tmp_path, monkeypatch):
    config_path = tmp_path / "routes.json"
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.delenv("BACKEND_CATALOG", raising=False)
    with ThreadedServer(make_redirect_backend("sample-a")) as backend, ThreadedServer(
        create_app()
    ) as gateway:
        yield gateway, config_path, backend


def write_mount_config(path, backend_url, routes, version=1):
    data = {
        "version": version,
        "published_at": "2026-01-01T00:00:00+00:00",
        "backends": [{"name": "sample-a", "url": backend_url}],
        "routes": routes,
    }
    parse_config(data)
    atomic_write_json(str(path), data)


def test_mount_strips_prefix_and_keeps_query(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/apps/demo", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.get(gateway.url + "/apps/demo/echo/x?a=1&b=2", timeout=10)
    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "/echo/x"
    assert payload["query"] == {"a": ["1"], "b": ["2"]}
    assert response.headers["X-Gateway-Route"] == "/apps/demo"


def test_mount_entry_without_slash_redirects_and_preserves_query(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/apps/demo", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.get(
        gateway.url + "/apps/demo?z=9", allow_redirects=False, timeout=10
    )
    assert response.status_code == 308
    assert response.headers["Location"] == "/apps/demo/?z=9"

    session = http.Session()
    followed = session.get(gateway.url + "/apps/demo?z=9", timeout=10)
    assert followed.status_code == 200
    assert followed.url == gateway.url + "/apps/demo/?z=9"
    assert followed.json()["path"] == "/"


def test_mount_entry_with_slash_opens_directly(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/apps/demo", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.get(gateway.url + "/apps/demo/", timeout=10)
    assert response.status_code == 200
    assert response.json()["path"] == "/"


def test_relative_form_post_stays_on_original_entry(mounted_gateway):
    gateway, path, backend = mounted_gateway
    routes = [
        {"path": "/entry-one", "match": "prefix",
         "backend": "sample-a", "mount": True},
        {"path": "/entry-two", "match": "prefix",
         "backend": "sample-a", "mount": True},
    ]
    write_mount_config(path, backend.url, routes)
    session = http.Session()

    for entry in ("/entry-one", "/entry-two"):
        # Browser flow: entry without slash -> 308 (POST method preserved),
        # then the relative form posts to "<entry>/" and follows the PRG 302.
        response = session.post(
            gateway.url + entry,
            data={"name": "你好"},
            allow_redirects=True,
            timeout=10,
        )
        assert response.status_code == 200
        # urllib drops the fragment from .url; the Location itself keeps it.
        assert response.url.startswith(
            gateway.url + entry + "/result?submitted="
        )
        assert response.json()["path"] == "/result"
        assert response.json()["query"]["submitted"] == ["你好"]
        history_locations = [r.headers["Location"] for r in response.history]
        assert any(
            location.endswith("#done") and entry + "/result" in location
            for location in history_locations
        )


def test_relative_location_is_rebased_under_mount(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/apps/demo", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.get(
        gateway.url + "/apps/demo/redirect/relative",
        allow_redirects=False,
        timeout=10,
    )
    assert response.status_code == 302
    assert response.headers["Location"] == (
        "/apps/demo/result?came=relative#frag"
    )


def test_absolute_location_to_backend_becomes_public_mount_url(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/apps/demo", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.get(
        gateway.url + "/apps/demo/redirect/absolute",
        allow_redirects=False,
        timeout=10,
    )
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith(gateway.url + "/apps/demo/result?")
    assert "came=absolute" in location and location.endswith("#abs")
    assert backend.url not in location  # internal origin must not leak


def test_two_mounts_rewrite_same_upstream_location_differently(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [
            {"path": "/one", "match": "prefix",
             "backend": "sample-a", "mount": True},
            {"path": "/two", "match": "prefix",
             "backend": "sample-a", "mount": True},
        ],
    )
    first = http.get(gateway.url + "/one/redirect/absolute",
                     allow_redirects=False, timeout=10)
    second = http.get(gateway.url + "/two/redirect/absolute",
                      allow_redirects=False, timeout=10)
    assert first.headers["Location"].startswith(gateway.url + "/one/result")
    assert second.headers["Location"].startswith(gateway.url + "/two/result")


def test_offsite_location_is_returned_verbatim(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/apps/demo", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.get(
        gateway.url + "/apps/demo/redirect/offsite",
        allow_redirects=False,
        timeout=10,
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "https://example.com/ext?keep=1#ext"


def test_non_mounted_route_keeps_prefix_when_forwarding(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/plain", "match": "prefix",
          "backend": "sample-a", "mount": False}],
    )
    response = http.get(gateway.url + "/plain/deep?x=1", timeout=10)
    assert response.status_code == 200
    assert response.json()["path"] == "/plain/deep"


def test_root_mount_forwards_everything_at_backend_root(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.get(gateway.url + "/echo/root?k=v", timeout=10)
    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "/echo/root"
    assert payload["query"] == {"k": ["v"]}


def test_mounted_post_body_is_forwarded(mounted_gateway):
    gateway, path, backend = mounted_gateway
    write_mount_config(
        path,
        backend.url,
        [{"path": "/apps/demo", "match": "prefix",
          "backend": "sample-a", "mount": True}],
    )
    response = http.post(
        gateway.url + "/apps/demo/echo/in",
        data=b"raw-bytes",
        timeout=10,
    )
    assert response.status_code == 200
