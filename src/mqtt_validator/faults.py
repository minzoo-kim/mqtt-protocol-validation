from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ToxiproxyError(RuntimeError):
    """Raised when the Toxiproxy control API cannot apply a network fault."""


class ToxiproxyController:
    def __init__(self, api_url: str, *, request_timeout_s: float = 2.0) -> None:
        self.api_url = api_url.rstrip("/")
        self.request_timeout_s = request_timeout_s

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.api_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.request_timeout_s) as response:
                body = response.read()
        except HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            raise ToxiproxyError(
                f"Toxiproxy {method} {path} returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ToxiproxyError(
                f"Toxiproxy {method} {path} failed: {exc}"
            ) from exc
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def wait_ready(self, timeout_s: float) -> str:
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                response = self._request("GET", "/version") or {}
                return str(response.get("version", "unknown"))
            except ToxiproxyError as exc:
                last_error = exc
                time.sleep(0.1)
        raise ToxiproxyError(
            f"Toxiproxy did not become ready within {timeout_s}s: {last_error}"
        )

    def reset(self) -> None:
        self._request("POST", "/reset", {})

    def create_proxy(
        self,
        *,
        name: str,
        listen: str,
        upstream: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/proxies",
            {
                "name": name,
                "listen": listen,
                "upstream": upstream,
                "enabled": True,
            },
        )
        if response is None:
            raise ToxiproxyError("Toxiproxy returned an empty create response")
        return response

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        response = self._request(
            "PATCH",
            f"/proxies/{quote(name, safe='')}",
            {"enabled": enabled},
        )
        if response is None:
            raise ToxiproxyError("Toxiproxy returned an empty update response")
        return response

    def delete_proxy(self, name: str) -> None:
        self._request(
            "DELETE",
            f"/proxies/{quote(name, safe='')}",
            allow_not_found=True,
        )
