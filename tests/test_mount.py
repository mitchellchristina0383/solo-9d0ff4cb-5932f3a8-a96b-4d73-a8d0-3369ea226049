from __future__ import annotations

import pytest

from common.config import (
    ConfigValidationError,
    Route,
    find_mount_conflicts,
    parse_config,
)
from gateway.mount import rewrite_location, strip_mount_prefix

CATALOG = {
    "sample-a": "http://sample-a:9000",
    "sample-b": "http://sample-b:9000",
}


def base_config(**overrides):
    data = {
        "schema_version": 1,
        "version": 3,
        "published_at": "2026-01-01T00:00:00+00:00",
        "backends": [
            {"name": "sample-a", "url": "http://sample-a:9000"},
            {"name": "sample-b", "url": "http://sample-b:9000"},
        ],
        "routes": [
            {"path": "/a", "match": "prefix", "backend": "sample-a"},
            {"path": "/b", "match": "prefix", "backend": "sample-b"},
        ],
    }
    data.update(overrides)
    return data


# --- strip_mount_prefix ----------------------------------------------------

@pytest.mark.parametrize(
    "raw_path,mount,expected",
    [
        ("/apps/demo/", "/apps/demo", "/"),
        ("/apps/demo", "/apps/demo", "/"),
        ("/apps/demo/submit", "/apps/demo", "/submit"),
        ("/apps/demo/a/b?x=1", "/apps/demo", "/a/b?x=1"),
        ("/", "/", "/"),
        ("/submit?x=1", "/", "/submit?x=1"),
        ("/demo/a%2Fb", "/demo", "/a%2Fb"),
        ("/demo/%E4%B8%AD", "/demo", "/%E4%B8%AD"),
    ],
)
def test_strip_mount_prefix(raw_path, mount, expected):
    assert strip_mount_prefix(raw_path, mount) == expected


# --- rewrite_location ------------------------------------------------------

PUBLIC = {"public_scheme": "http", "public_host": "localhost:8080"}


def test_relative_path_is_rebased_under_mount():
    out = rewrite_location(
        "result?submitted=hi#done",
        backend_url="http://sample-a:9000",
        mount_path="/apps/demo",
        base_path="/submit",
        **PUBLIC,
    )
    assert out == "/apps/demo/result?submitted=hi#done"


def test_relative_parent_path_is_rebased():
    out = rewrite_location(
        "../result?came=relative#frag",
        backend_url="http://sample-a:9000",
        mount_path="/apps/demo",
        base_path="/redirect/relative",
        **PUBLIC,
    )
    # The ".." is resolved against the upstream page before rebase, so the
    # public URL carries no dot segment.
    assert out == "/apps/demo/result?came=relative#frag"


def test_relative_location_on_root_mount():
    out = rewrite_location(
        "result?x=1",
        backend_url="http://sample-a:9000",
        mount_path="/",
        base_path="/submit",
        **PUBLIC,
    )
    assert out == "/result?x=1"


def test_fragment_only_relative_location_kept():
    out = rewrite_location(
        "#section",
        backend_url="http://sample-a:9000",
        mount_path="/apps/demo",
        base_path="/page",
        **PUBLIC,
    )
    assert out == "/apps/demo/page#section"


def test_absolute_location_to_backend_is_rewritten():
    out = rewrite_location(
        "http://sample-a:9000/result?came=absolute#abs",
        backend_url="http://sample-a:9000",
        mount_path="/apps/demo",
        base_path="/redirect/absolute",
        **PUBLIC,
    )
    assert out == "http://localhost:8080/apps/demo/result?came=absolute#abs"


def test_absolute_location_default_port_matches_backend():
    out = rewrite_location(
        "http://sample-a/result",
        backend_url="http://sample-a:80",
        mount_path="/apps/demo",
        base_path="/x",
        **PUBLIC,
    )
    assert out == "http://localhost:8080/apps/demo/result"


def test_network_path_reference_to_backend_is_rewritten():
    out = rewrite_location(
        "//sample-a:9000/result?x=1",
        backend_url="http://sample-a:9000",
        mount_path="/apps/demo",
        base_path="/x",
        **PUBLIC,
    )
    assert out == "http://localhost:8080/apps/demo/result?x=1"


def test_offsite_absolute_location_passes_through():
    original = "https://example.com/external?keep=1#ext"
    out = rewrite_location(
        original,
        backend_url="http://sample-a:9000",
        mount_path="/apps/demo",
        base_path="/x",
        **PUBLIC,
    )
    assert out == original


def test_offsite_network_path_reference_passes_through():
    original = "//cdn.example.com/lib.js"
    out = rewrite_location(
        original,
        backend_url="http://sample-a:9000",
        mount_path="/apps/demo",
        base_path="/x",
        **PUBLIC,
    )
    assert out == original


def test_non_http_scheme_passes_through():
    for original in ("mailto:a@b.com", "javascript:alert(1)"):
        out = rewrite_location(
            original,
            backend_url="http://sample-a:9000",
            mount_path="/apps/demo",
            base_path="/x",
            **PUBLIC,
        )
        assert out == original


def test_two_mounts_get_distinct_public_paths():
    # The same backend Location must be rewritten under whichever mount
    # served the current request.
    location = "http://sample-a:9000/result?x=1#f"
    common = dict(backend_url="http://sample-a:9000", base_path="/x", **PUBLIC)
    first = rewrite_location(location, mount_path="/entry-one", **common)
    second = rewrite_location(location, mount_path="/entry-two", **common)
    assert first == "http://localhost:8080/entry-one/result?x=1#f"
    assert second == "http://localhost:8080/entry-two/result?x=1#f"


# --- config validation -----------------------------------------------------

def test_mount_flag_is_parsed_and_serialized():
    config = parse_config(
        base_config(
            routes=[
                {"path": "/demo", "match": "prefix",
                 "backend": "sample-a", "mount": True}
            ]
        ),
        catalog=CATALOG,
    )
    route = config.routes[0]
    assert route.mount is True
    assert config.to_dict()["routes"][0]["mount"] is True


def test_mount_defaults_to_false_for_legacy_config():
    config = parse_config(base_config(), catalog=CATALOG)
    assert all(route.mount is False for route in config.routes)
    assert all(
        route.get("mount") is False for route in config.to_dict()["routes"]
    )


def test_mount_requires_prefix_match():
    with pytest.raises(ConfigValidationError) as excinfo:
        parse_config(
            base_config(
                routes=[
                    {"path": "/demo", "match": "exact",
                     "backend": "sample-a", "mount": True}
                ]
            ),
            catalog=CATALOG,
        )
    assert any("挂载" in message for message in excinfo.value.errors)


def test_non_boolean_mount_rejected():
    with pytest.raises(ConfigValidationError):
        parse_config(
            base_config(
                routes=[
                    {"path": "/demo", "backend": "sample-a", "mount": "yes"}
                ]
            ),
            catalog=CATALOG,
        )


def test_blocking_rule_under_mount_is_conflict():
    with pytest.raises(ConfigValidationError) as excinfo:
        parse_config(
            base_config(
                routes=[
                    {"path": "/demo", "backend": "sample-a", "mount": True},
                    {"path": "/demo/api", "match": "exact",
                     "backend": "sample-b"},
                ]
            ),
            catalog=CATALOG,
        )
    message = "；".join(excinfo.value.errors)
    assert "/demo" in message and "/demo/api" in message


def test_sibling_path_is_not_a_mount_conflict():
    config = parse_config(
        base_config(
            routes=[
                {"path": "/demo", "backend": "sample-a", "mount": True},
                {"path": "/demolition", "backend": "sample-b"},
            ]
        ),
        catalog=CATALOG,
    )
    assert find_mount_conflicts(list(config.routes)) == []


def test_two_mounts_of_same_backend_are_allowed():
    config = parse_config(
        base_config(
            routes=[
                {"path": "/one", "backend": "sample-a", "mount": True},
                {"path": "/two", "backend": "sample-a", "mount": True},
            ]
        ),
        catalog=CATALOG,
    )
    assert all(isinstance(route, Route) and route.mount for route in config.routes)
