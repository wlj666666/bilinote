"""Image-only outbound checks. Deliberately allow private networks and all platforms.

This is a minimal denylist, not a general SSRF firewall. Admin-configured proxies
remain usable; their remote DNS resolution cannot be pinned by this client.
"""
import ipaddress
import socket
from urllib.parse import unquote, urljoin, urlsplit

import anyio
import httpcore
import httpx
from fastapi import HTTPException, Response


_LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")
_BLOCKED_IPS = {
    ipaddress.ip_address(value)
    for value in ("0.0.0.0", "::", "::1", "169.254.169.254", "fd00:ec2::254")
}


def check_image_host(host: str) -> None:
    """Check the actual hostname, never words appearing in a path or query."""
    host = unquote(host).lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise HTTPException(403, "图片目标地址已禁用")
    try:
        # Strip IPv6 scope IDs and recognize IPv4-mapped IPv6 addresses.
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            # Match the OS resolver's legacy numeric IPv4 forms (127.1, hex,
            # octal, decimal integers), rather than treating them as domains.
            address = ipaddress.ip_address(socket.inet_aton(host))
        except OSError:
            return
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address in _BLOCKED_IPS or (
        isinstance(address, ipaddress.IPv4Address) and address in _LOOPBACK_V4
    ):
        raise HTTPException(403, "图片目标地址已禁用")


async def _checked_addresses(host: str, port: int) -> list[str]:
    check_image_host(host)
    records = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = []
    for family, _, _, _, sockaddr in records:
        address = sockaddr[0]
        check_image_host(address)
        # Keep the interface for allowed, scoped IPv6 destinations.
        if family == socket.AF_INET6 and len(sockaddr) > 3 and sockaddr[3] and "%" not in address:
            address = f"{address}%{sockaddr[3]}"
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise socket.gaierror("No image destination addresses")
    return addresses


async def _check_request_target(request: httpx.Request) -> None:
    check_image_host(request.url.host)
    try:
        with anyio.fail_after(request.extensions.get("timeout", {}).get("connect", 10.0)):
            await _checked_addresses(
                request.url.raw_host.decode("ascii"),
                request.url.port or (443 if request.url.scheme == "https" else 80),
            )
    except (socket.gaierror, TimeoutError):
        # A configured proxy may resolve names unavailable to local DNS. Do not
        # break those sites. Direct connections still resolve/check below.
        pass


async def _check_redirect_target(response: httpx.Response) -> None:
    if response.has_redirect_location:
        # Inspect the raw Location before HTTPX normalizes/rejects legacy IP
        # spellings, so denied targets consistently return 403 instead of 500.
        target = urljoin(str(response.request.url), response.headers["location"])
        check_image_host(urlsplit(target).hostname or "")


class _CheckedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend):
        self.backend = backend

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            with anyio.fail_after(timeout):
                addresses = await _checked_addresses(host, port)
                last_error = None
                deadline = anyio.current_time() + timeout if timeout is not None else None
                for address in addresses:
                    remaining = max(0, deadline - anyio.current_time()) if deadline is not None else None
                    try:
                        # Connect to the checked IP, not a newly resolved name.
                        # HTTP Host, TLS SNI and certificate checks retain the
                        # original hostname because the request URL is unchanged.
                        return await self.backend.connect_tcp(
                            address, port, timeout=remaining,
                            local_address=local_address, socket_options=socket_options,
                        )
                    except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                        last_error = exc
                raise last_error
        except socket.gaierror as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout() from exc

    async def connect_unix_socket(self, *args, **kwargs):
        return await self.backend.connect_unix_socket(*args, **kwargs)

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


class ImageProxyClient(httpx.AsyncClient):
    """Check every redirect while preserving HTTPX environment proxy/NO_PROXY routing."""

    def __init__(self, *args, **kwargs):
        hooks = {key: list(value) for key, value in kwargs.pop("event_hooks", {}).items()}
        hooks.setdefault("request", []).insert(0, _check_request_target)
        hooks.setdefault("response", []).insert(0, _check_redirect_target)
        kwargs["event_hooks"] = hooks
        super().__init__(*args, **kwargs)

    def build_request(self, method, url, *args, **kwargs):
        check_image_host(urlsplit(str(url)).hostname or "")
        return super().build_request(method, url, *args, **kwargs)

    def _init_transport(self, *args, **kwargs):
        transport = super()._init_transport(*args, **kwargs)
        if isinstance(transport, httpx.AsyncHTTPTransport):
            # HTTPX 0.28.1 / httpcore 1.0.7 (pinned in requirements.txt) expose
            # no public network-backend option. Wrap only the direct pool;
            # proxy pools must still be able to connect to a local admin proxy.
            transport._pool._network_backend = _CheckedNetworkBackend(transport._pool._network_backend)
        return transport


async def fetch_proxy_image(url: str, user_agent: str) -> Response:
    try:
        async with ImageProxyClient(timeout=10.0, follow_redirects=True, max_redirects=20) as client:
            response = await client.get(url, headers={
                "Referer": "https://www.bilibili.com/",
                "User-Agent": user_agent,
            })
            if response.status_code != 200:
                raise HTTPException(response.status_code, "图片获取失败")
            content_type = response.headers.get("Content-Type", "image/jpeg")
            if content_type.split(";", 1)[0].lower() not in {
                "image/jpeg", "image/png", "image/webp", "image/gif", "image/avif",
            }:
                raise HTTPException(400, "仅支持代理图片")
            return Response(response.content, headers={
                "Content-Type": content_type, "Cache-Control": "no-store",
            })
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "图片获取失败") from None
