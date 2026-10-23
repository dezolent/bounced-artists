"""Bounded HTTP with fixed origins, safe errors and in-memory credentials."""
import base64
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CollectionError(Exception):
    """Only fixed, non-sensitive error codes may cross the collector boundary."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Budget:
    def __init__(self, limit=1000):
        if not 1 <= limit <= 100000:
            raise CollectionError("invalid_request_limit")
        self.limit = limit
        self.used = 0

    def take(self):
        if self.used >= self.limit:
            raise CollectionError("request_limit")
        self.used += 1


class JsonClient:
    def __init__(self, origin, headers=None, budget=None):
        self.origin = origin
        self.headers = headers or {}
        self.budget = budget or Budget()
        self.opener = build_opener(NoRedirect())

    def request(self, path, params=None, data=None):
        # Neither scraped links nor pagination URLs are allowed to choose an origin.
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in (":", "?", "#", "\\")):
            raise CollectionError("invalid_request_path")
        url = self.origin + path
        if params:
            url += "?" + urlencode(params)
        for attempt in range(3):
            self.budget.take()
            request = Request(url, data=data, headers={"Accept": "application/json", **self.headers})
            try:
                with self.opener.open(request, timeout=45) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise CollectionError("response_too_large")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise CollectionError("invalid_response_shape")
                if result.get("error") or result.get("code") in {"quota_exceeded", "invalid_api_key", "missing_api_key", "subscription_expired"}:
                    raise CollectionError("provider_error")
                return result
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    try:
                        delay = float(exc.headers.get("Retry-After", 2 ** attempt))
                    except (TypeError, ValueError):
                        delay = 2 ** attempt
                    if not 0 <= delay <= 30:
                        raise CollectionError("rate_limit_wait_exceeded") from None
                    time.sleep(delay)
                    continue
                raise CollectionError(f"http_{exc.code}") from None
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise CollectionError("network_error") from None
            except (ValueError, UnicodeError):
                raise CollectionError("invalid_json") from None

    def get(self, path, **params):
        return self.request(path, params)


def omkar_client(platform, budget):
    key = os.environ.get("OMKAR_API_KEY")
    if not key:
        raise CollectionError("missing_omkar_credentials")
    return JsonClient(f"https://{platform}-scraper.omkar.cloud", {"API-Key": key}, budget).get


class SpotifyClient:
    def __init__(self, budget):
        self.budget = budget
        self.token = None
        self.expires = 0

    def get(self, path, **params):
        if not self.token or time.monotonic() >= self.expires:
            client_id = os.environ.get("SPOTIFY_CLIENT_ID")
            secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
            if not client_id or not secret:
                raise CollectionError("missing_spotify_credentials")
            basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            response = JsonClient("https://accounts.spotify.com", {
                "Authorization": "Basic " + basic,
                "Content-Type": "application/x-www-form-urlencoded",
            }, self.budget).request("/api/token", data=b"grant_type=client_credentials")
            token, expiry = response.get("access_token"), response.get("expires_in")
            if not isinstance(token, str) or not isinstance(expiry, (int, float)) or expiry <= 0:
                raise CollectionError("invalid_spotify_token_response")
            self.token, self.expires = token, time.monotonic() + max(1, expiry - 60)
        params.setdefault("market", "US")
        return JsonClient("https://api.spotify.com/v1", {"Authorization": "Bearer " + self.token}, self.budget).get(path, **params)
