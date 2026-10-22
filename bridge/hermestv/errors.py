import httpx


def brief(e: BaseException) -> str:
    """The HTTP status or the error type only: an httpx error text holds the request URL, which can hold a key."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"http {e.response.status_code}"
    return type(e).__name__
