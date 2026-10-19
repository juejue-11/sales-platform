"""Public-source HTTP access with URL and download budget enforcement."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .config import ResearchError, DEFAULT_BUDGETS


def safe_url(url: str, *, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ResearchError("invalid_source_url")
    try:
        port = parts.port
    except ValueError:
        raise ResearchError("invalid_source_url") from None
    expected = 443 if parts.scheme == "https" else 80
    if port not in (None, expected):
        raise ResearchError("invalid_source_port")
    host = parts.hostname.lower().rstrip(".")
    host_for_url = f"[{host}]" if ":" in host else host
    netloc = host_for_url if port is None else f"{host_for_url}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    values = [str(item).lower().strip().rstrip(".") for item in allowed if str(item).strip()]
    if not values:
        return True
    for value in values:
        if value.startswith("*.") and (host == value[2:] or host.endswith("." + value[2:])):
            return True
        if host == value:
            return True
    return False


def is_mock_transport(client: httpx.AsyncClient) -> bool:
    transport = getattr(client, "_transport", None)
    return transport is not None and transport.__class__.__name__ == "MockTransport"


async def validate_network_url(url: str, allowed_hosts: Iterable[str], client: httpx.AsyncClient) -> str:
    normalized = safe_url(url)
    host = urlsplit(normalized).hostname or ""
    if not host_allowed(host, allowed_hosts):
        raise ResearchError("attachment_outside_source")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            info = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(item[4][0]) for item in info]
        except OSError:
            # MockTransport does not perform DNS.  Real clients must resolve
            # successfully before a request is sent.
            if is_mock_transport(client):
                addresses = []
            else:
                raise ResearchError("source_unreachable") from None
    if any(not address.is_global for address in addresses):
        raise ResearchError("non_public_source")
    return normalized


class NetworkTransport:
    """Own an HTTP client and enforce network, redirect, and byte limits."""

    def __init__(self, client: httpx.AsyncClient | None = None,
                 budgets: dict[str, Any] | None = None):
        self.client = client
        self._own_client = client is None
        self.budgets = {**DEFAULT_BUDGETS, **(budgets or {})}
        self.calls = 0
        self.download_bytes = 0

    async def close(self) -> None:
        if self._own_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    async def get_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)
        return self.client

    def budget_check(self, *, calls: int = 1, bytes_count: int = 0) -> None:
        if self.calls + calls > int(self.budgets.get("max_calls", DEFAULT_BUDGETS["max_calls"])):
            raise ResearchError("budget_exhausted")
        if self.download_bytes + bytes_count > int(self.budgets.get("max_download_bytes", DEFAULT_BUDGETS["max_download_bytes"])):
            raise ResearchError("budget_exhausted")

    @staticmethod
    def allowed_hosts(cfg: dict[str, Any], endpoint: str | None = None) -> list[str]:
        values: list[str] = []
        for key in ("allowed_hosts", "allowed_domains", "domains", "hosts"):
            item = cfg.get(key)
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, list):
                values.extend(str(value) for value in item)
        if endpoint:
            host = urlsplit(endpoint).hostname
            if host:
                values.append(host)
        base = cfg.get("base_url")
        if isinstance(base, str):
            try:
                host = urlsplit(safe_url(base)).hostname
                if host:
                    values.append(host)
            except ResearchError:
                pass
        return list(dict.fromkeys(values))

    async def request(self, method: str, url: str, cfg: dict[str, Any], *,
                      json_body: Any = None,
                      params: dict[str, Any] | None = None) -> tuple[bytes, str, str, int]:
        client = await self.get_client()
        endpoint = safe_url(url)
        allowed = self.allowed_hosts(cfg, endpoint)
        current = endpoint
        redirects = 0
        while True:
            if redirects > int(self.budgets.get("max_redirects", DEFAULT_BUDGETS["max_redirects"])):
                raise ResearchError("redirect_limit")
            current = await validate_network_url(current, allowed, client)
            self.budget_check()
            self.calls += 1
            headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
            try:
                async with client.stream(method, current, headers=headers, json=json_body,
                                         params=params if redirects == 0 else None,
                                         follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ResearchError("redirect_without_location")
                        next_url = urljoin(current, location)
                        current = await validate_network_url(next_url, allowed, client)
                        if response.status_code == 303 or (response.status_code in {301, 302} and method == "POST"):
                            method, json_body = "GET", None
                        redirects += 1
                        continue
                    declared_size = response.headers.get("content-length", "")
                    if declared_size.isdigit():
                        self.budget_check(calls=0, bytes_count=int(declared_size))
                    chunks: list[bytes] = []
                    async for chunk in response.aiter_bytes():
                        self.budget_check(calls=0, bytes_count=len(chunk))
                        self.download_bytes += len(chunk)
                        chunks.append(chunk)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    return b"".join(chunks), content_type, current, response.status_code
            except httpx.HTTPError as exc:
                raise ResearchError("source_unreachable", str(exc)) from exc


__all__ = [
    "NetworkTransport",
    "host_allowed",
    "is_mock_transport",
    "safe_url",
    "validate_network_url",
]
