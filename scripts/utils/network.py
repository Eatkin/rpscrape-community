from collections.abc import Sequence
from random import choice
from random import uniform
from time import monotonic
from time import sleep

from curl_cffi import BrowserTypeLiteral
from curl_cffi import Response
from curl_cffi import Session


class PersistentBlockError(Exception):
    pass


# Racing Post signals throttling/blocking with 406 (IP block), 429 or 403.
BLOCK_STATUSES = frozenset({403, 406, 429})


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
        retries: int = 7,
        retry_delay: float = 1.4,
    ) -> None:
        self.session: Session = Session(impersonate=choice(BROWSERS))
        self.timeout: int = timeout
        self.min_interval: float = min_interval
        self.jitter: float = jitter
        self.retries: int = retries
        self.retry_delay: float = retry_delay
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
        retries: int | None = None,
        delay: float | None = None,
    ) -> tuple[int, Response]:
        retries = self.retries if retries is None else retries
        delay = self.retry_delay if delay is None else delay
        backoff = 1
        status = None
        for attempt in range(1, retries):
            self._pace()

            response = self.session.get(
                url,
                allow_redirects=allow_redirects,
                timeout=self.timeout,
            )

            status = response.status_code
            if status not in BLOCK_STATUSES:
                return status, response

            if attempt < retries - 1:
                sleep(max(delay * backoff, self._retry_after(response)))
                backoff *= 2

        raise PersistentBlockError(f"blocked (status {status}) for {retries - 1} attempts on {url}")

    @staticmethod
    def _retry_after(response: Response) -> float:
        try:
            return float(response.headers.get("Retry-After", 0))
        except ValueError:
            return 0.0
