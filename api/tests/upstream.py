"""Upstream HTTP answers and failures as httpx gives them (#645), for tests."""

import ssl

import httpx


def status_error(status: int, url: str = "https://upstream.example.com/x") -> httpx.HTTPStatusError:
    """What ``http.request`` raises for an error status."""
    request = httpx.Request("GET", url)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status} error", request=request, response=response)


def certificate_error() -> httpx.ConnectError:
    """An untrusted certificate: httpx's ConnectError, caused by the ssl.SSLError."""
    try:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLError as exc:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]") from exc
    except httpx.ConnectError as error:
        return error


class Bound:
    """A fake session as ``http.client(verify)``: its ``request`` also gets *verify*."""

    def __init__(self, fake, verify):  # type: ignore[no-untyped-def]
        self.fake, self.verify = fake, verify

    def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        return self.fake.request(method, url, verify=self.verify, **kwargs)
