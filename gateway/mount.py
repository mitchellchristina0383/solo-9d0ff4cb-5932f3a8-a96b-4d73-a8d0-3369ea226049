"""Prefix-stripping and upstream redirect rewriting for mounted routes.

A *mounted* prefix route (``mount: true``) exposes an application that itself
runs at the root of its own origin underneath a gateway sub-path.  The gateway
therefore:

1. strips the mount prefix before forwarding (the upstream sees its own root
   layout) while keeping the raw query string untouched;
2. rewrites upstream ``Location`` headers that point back at the forwarded
   backend so the browser keeps using the public mounted URL, preserving the
   query string and fragment.  Redirects to other origins are returned as-is.

The functions here deliberately use only the raw request target (not the
percent-decoded ``request.path``) so upstream octets are preserved.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}
_HTTP_SCHEMES = ("http", "https")


def strip_mount_prefix(raw_path: str, mount_path: str) -> str:
    """Return the upstream path with *mount_path* removed.

    Segment based rather than a naive string slice: percent-encoded slashes
    (``%2F``) stay inside one segment, and the result always starts with ``/``
    (the upstream root when the request hits the mount entry itself).
    """
    mount_segments = len([segment for segment in mount_path.split("/") if segment])
    request_segments = raw_path.split("/")
    # An absolute path's first split element is the empty string before "/".
    remaining = request_segments[1 + mount_segments:]
    return "/" + "/".join(remaining)


def _origin(parts) -> tuple[str, str, int]:
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is None:
        port = _DEFAULT_PORTS.get(scheme, 0)
    return scheme, host, port


def _join_public_path(mount_path: str, upstream_path: str) -> str:
    """Prefix an upstream path with the public mount path."""
    base = mount_path.rstrip("/")
    if not upstream_path:
        return base or "/"
    if upstream_path.startswith("/"):
        return base + upstream_path if base else upstream_path
    # Relative path segment (e.g. "other"); RFC 3986 merges it onto the base.
    return (base + "/" + upstream_path) if base else "/" + upstream_path


def rewrite_location(
    location: str,
    *,
    backend_url: str,
    mount_path: str,
    public_scheme: str,
    public_host: str,
    base_path: str,
) -> str:
    """Rewrite one upstream ``Location`` value for a mounted route.

    Absolute or network-path references targeting the forwarded backend become
    public mounted URLs; references to any other origin or non-HTTP schemes
    pass through untouched.  Relative references stay relative and are resolved
    against *base_path* (the upstream path that produced this response) before
    being rebased under the mount.  Query strings and fragments are preserved.
    """
    parts = urlsplit(location)
    backend_parts = urlsplit(backend_url)

    if parts.scheme or parts.netloc:
        # Absolute or network-path reference.
        if parts.scheme and parts.scheme.lower() not in _HTTP_SCHEMES:
            return location
        # A network-path reference inherits the backend's scheme for the
        # origin comparison.
        if not parts.scheme and parts.netloc:
            check_parts = urlsplit(backend_parts.scheme + ":" + location)
        else:
            check_parts = parts
        if _origin(check_parts) != _origin(backend_parts):
            # Different host/port: off-site redirect, do not touch it.
            return location
        upstream_path = parts.path or "/"
        public_path = _join_public_path(mount_path, upstream_path)
        return urlunsplit(
            (public_scheme, public_host, public_path, parts.query, parts.fragment)
        )

    # Relative reference.  Resolve dot segments / fragment-only / same-
    # directory references against the upstream page that issued the redirect,
    # then rebase the resolved absolute upstream path under the mount.
    resolved = urlsplit(urljoin("http://upstream-root" + base_path, location))
    public_path = _join_public_path(mount_path, resolved.path or "/")
    return urlunsplit(
        ("", "", public_path, resolved.query, resolved.fragment)
    )
