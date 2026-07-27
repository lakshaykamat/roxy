from collections.abc import Mapping

import httpx


async def fetch_web_response(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    """Download a bounded web response with decoded content ready to read."""
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError("The source is larger than the allowed limit.")
            response_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-encoding", "content-length"}
            }
            return httpx.Response(
                response.status_code,
                headers=response_headers,
                content=bytes(body),
                request=response.request,
            )
