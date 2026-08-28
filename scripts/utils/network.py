from collections.abc import Sequence
from random import choice
from random import uniform
from time import monotonic
from time import sleep

from curl_cffi import BrowserTypeLiteral
from curl_cffi import Response
from curl_cffi import Session


class Persistent406Error(Exception):
    pass


BROWSERS: Sequence[BrowserTypeLiteral] = (
    "edge",
    "chrome",
    "firefox",
    "safari",
)


class NetworkClient:
    def __init__(
        self,
        *,
        timeout: int = 14,
        min_interval: float = 2.0,
        jitter: float = 1.0,
    ) -> None:
        self.session: Session = Session(impersonate=choice(BROWSERS))
        self.timeout: int = timeout
        self.min_interval: float = min_interval
        self.jitter: float = jitter
        self._last_request: float | None = None

    def _pace(self) -> None:
        if self._last_request is not None:
            elapsed = monotonic() - self._last_request
            wait = self.min_interval + uniform(0, self.jitter) - elapsed
            if wait > 0:
                sleep(wait)

        self._last_request = monotonic()

    def get(
        self,
        url: str,
        allow_redirects: bool = True,
        retries: int = 7,
        delay: float = 1.4,
    ) -> tuple[int, Response]:
        backoff = 1
        for attempt in range(1, retries):
            self._pace()

            response = self.session.get(
                url,
                allow_redirects=allow_redirects,
                timeout=self.timeout,
            )

            if response.status_code != 406:
                return response.status_code, response

            if attempt < retries:
                sleep(delay * backoff)
                backoff *= 2

        raise Persistent406Error(f"received 406 for {retries} attempts on {url}")
