"""Bounded HTTP with fixed origins, safe errors and in-memory credentials."""
import base64
import json
import os
import random
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CollectionError(Exception):
    """Only fixed, non-sensitive error codes may cross the collector boundary."""


PROVIDER_ERROR_CODES = frozenset({
    "provider_login_required", "provider_blocked", "provider_upstream_error", "provider_internal_error",
})

SAFE_ERROR_CODES = {
    "request_limit", "invalid_request_limit", "invalid_job_limit", "invalid_request_path",
    "network_error", "invalid_json", "invalid_response_shape", "provider_error",
    "response_too_large", "rate_limit_wait_exceeded", "missing_omkar_credentials",
    "missing_spotify_credentials", "invalid_spotify_token_response", "invalid_object_id",
    "invalid_collection_shape", "missing_pagination_cursor", "repeated_pagination_cursor",
    "pagination_limit", "invalid_apple_storefront", "invalid_profile_url",
    "invalid_artist_profile", "invalid_channel_profile", "invalid_user_profile",
    "missing_album_tracks", "private_profile", "provider_collection_truncated", "job_request_limit",
    "profile_identity_mismatch", "video_identity_mismatch", "music_identity_mismatch",
    "invalid_scraper_backend", "missing_scraper_url", "invalid_scraper_url", "unsupported_platform",
    "invalid_catalog_seed", "catalog_identity_mismatch", "invalid_audio_features", "spotify_audio_features_access_unavailable",
    "playlist_identity_mismatch", "missing_privacy_status", "time_limit", "job_time_limit", "invalid_time_limit",
} | PROVIDER_ERROR_CODES


def safe_error_code(error):
    code = str(error)
    return code if code in SAFE_ERROR_CODES or re.fullmatch(r"http_[1-5][0-9]{2}", code) else "collection_error"


def retry_delay(attempt, retry_after=None):
    try:
        delay = max(2 ** attempt, float(retry_after)) if retry_after is not None else 2 ** attempt
    except (TypeError, ValueError):
        delay = 2 ** attempt
    if not 0 <= delay <= 30:
        raise CollectionError("rate_limit_wait_exceeded")
    return min(30, delay + random.uniform(0, 0.5))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Budget:
    def __init__(self, limit=1000, *, parent=None, seconds=None):
        if not 1 <= limit <= 100000:
            raise CollectionError("invalid_request_limit")
        self.limit = limit
        self.used = 0
        self.parent = parent
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 86400):
            raise CollectionError("invalid_time_limit")
        self.deadline = time.monotonic() + seconds if seconds is not None else None

    def check_time(self):
        if self.parent is not None:
            self.parent.check_time()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise CollectionError("job_time_limit" if self.parent is not None else "time_limit")

    def take(self):
        self.check_time()
        if self.used >= self.limit:
            raise CollectionError("job_request_limit" if self.parent is not None else "request_limit")
        if self.parent is not None:
            self.parent.take()
        self.used += 1


REQUEST_ENDPOINTS = {
    "/api/token", "/artists/details", "/artists/discovered-on", "/artists/appears-on",
    "/artists/albums", "/albums/details", "/albums/tracks", "/tracks/details",
    "/users/details", "/users/tracks", "/users/playlists", "/users/spotlight",
    "/users/social-links", "/songs/details", "/artists/music-videos", "/artists/playlists",
    "/channels/details", "/channels/videos", "/channels/shorts", "/channels/live",
    "/channels/playlists", "/videos/details", "/users/profile", "/users/posts",
    "/users/reels", "/posts/details", "/artists/top-songs", "/users/videos",
    "/music/details", "/music/videos", "/playlists/videos", "/playlists/details",
    "/tracks/lyrics",
}
ENDPOINT_NAMES = {path.strip("/").replace("/", "-") for path in REQUEST_ENDPOINTS} | {"spotify-track", "spotify-audio-features", "musixmatch-track-lyrics", "request"}


def request_endpoint(path):
    if path in REQUEST_ENDPOINTS:
        return path.strip("/").replace("/", "-")
    if re.fullmatch(r"/audio-features/[A-Za-z0-9]{22}", path):
        return "spotify-audio-features"
    if path.startswith("/tracks/") and path.count("/") == 2:
        return "spotify-track"
    return "request"


class JsonClient:
    def __init__(self, origin, headers=None, budget=None, *, provider_errors=False):
        self.origin = origin
        self.headers = headers or {}
        self.budget = budget or Budget()
        self.opener = build_opener(NoRedirect())
        self.provider_errors = provider_errors
        self.unavailable_endpoints = {}

    def request(self, path, params=None, data=None):
        try:
            return self._request(path, params, data)
        except CollectionError as exc:
            exc.endpoint = request_endpoint(path)
            raise

    def _request(self, path, params=None, data=None):
        # Neither scraped links nor pagination URLs are allowed to choose an origin.
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in (":", "?", "#", "\\")):
            raise CollectionError("invalid_request_path")
        self.budget.check_time()
        if path in self.unavailable_endpoints:
            raise CollectionError(self.unavailable_endpoints[path])
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
                retry_after = (exc.headers or {}).get("Retry-After")
                code = ""
                try:
                    if self.provider_errors:
                        # Only our private route adapter emits these fixed codes.
                        # Never copy a provider error body/message into receipts.
                        body = json.loads(exc.read(4097))
                        candidate = body.get("error_code") if isinstance(body, dict) else None
                        if isinstance(candidate, str) and candidate in PROVIDER_ERROR_CODES:
                            code = candidate
                except (ValueError, UnicodeError, OSError):
                    pass
                finally:
                    exc.close()
                if code in {"provider_login_required", "provider_blocked"}:
                    # Upstream already exhausted its retries. Repeating this
                    # surface for every catalog item cannot fix a login/IP gate.
                    self.unavailable_endpoints[path] = code
                if code and code != "provider_upstream_error":
                    raise CollectionError(code) from None
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(retry_delay(attempt, retry_after))
                    continue
                raise CollectionError(code or f"http_{exc.code}") from None
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(retry_delay(attempt))
                    continue
                raise CollectionError("network_error") from None
            except (ValueError, UnicodeError):
                raise CollectionError("invalid_json") from None

    def get(self, path, **params):
        return self.request(path, params)


def omkar_client(platform, budget):
    from .backends import scraper_backend
    backend = scraper_backend(platform)
    if backend.mode not in {"hosted", "self_hosted"}:
        raise CollectionError("unsupported_platform")
    if backend.mode == "self_hosted":
        return JsonClient(backend.origin, {}, budget, provider_errors=True).get
    key = os.environ.get("OMKAR_API_KEY")
    if not key:
        raise CollectionError("missing_omkar_credentials")
    return JsonClient(backend.origin, {"API-Key": key}, budget).get


class SpotifyClient:
    def __init__(self, budget):
        self.budget = budget
        self.token = None
        self.expires = 0
        self.api_client = JsonClient("https://api.spotify.com/v1", budget=budget)

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
        if not path.startswith("/audio-features/"):
            params.setdefault("market", "US")
        self.api_client.headers["Authorization"] = "Bearer " + self.token
        return self.api_client.get(path, **params)
