"""HTTP client for Whatsthecall's login, presign, S3 PUT, and register flow."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


class UploadError(RuntimeError):
    pass


def normalize_site_url(site_url: str) -> str:
    parsed = urllib.parse.urlparse(site_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Enter a full Whatsthecall URL, including https:// or http://localhost.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote Whatsthecall URLs must use HTTPS.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Enter the site origin only, without a path or query.")
    return site_url.strip().rstrip("/")


class WhatsthecallClient:
    def __init__(self, site_url: str):
        self.site_url = normalize_site_url(site_url)
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def _post_json(self, path: str, payload: dict) -> dict:
        url = self.site_url + path
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                result = json.load(exc)
                reason = result.get("error", str(exc)) if isinstance(result, dict) else str(exc)
            except (ValueError, OSError):
                reason = str(exc)
            raise UploadError(f"{path}: {reason} (HTTP {exc.code})") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise UploadError(f"{path}: could not reach Whatsthecall: {exc}") from exc
        except (ValueError, OSError) as exc:
            raise UploadError(f"{path}: invalid response from Whatsthecall") from exc
        if not isinstance(result, dict):
            raise UploadError(f"{path}: invalid JSON response")
        return result

    def login(self, email: str, password: str) -> None:
        if not self._post_json("/api/auth/login", {"email": email, "password": password}).get("success"):
            raise UploadError("Login did not return success.")

    def presign(self, file: Path) -> tuple[str, str]:
        result = self._post_json(
            "/api/videos/presign", {"fileName": file.name, "fileType": "video/mp4"}
        )
        url, key = result.get("uploadUrl"), result.get("key")
        if not isinstance(url, str) or not isinstance(key, str):
            raise UploadError("Presign response did not contain an upload URL and key.")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or not (
            parsed.hostname.endswith(".amazonaws.com") or parsed.hostname.endswith(".amazonaws.com.cn")
        ):
            raise UploadError("Presign response did not contain a valid S3 HTTPS URL.")
        return url, key

    def put_video(self, upload_url: str, file: Path) -> None:
        parsed = urllib.parse.urlparse(upload_url)
        if parsed.scheme != "https" or not parsed.hostname or not (
            parsed.hostname.endswith(".amazonaws.com") or parsed.hostname.endswith(".amazonaws.com.cn")
        ):
            raise UploadError("Refusing a non-S3 upload URL.")
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=180)
        try:
            with file.open("rb") as handle:
                connection.request(
                    "PUT", parsed.path + ("?" + parsed.query if parsed.query else ""),
                    body=handle,
                    headers={"Content-Type": "video/mp4", "Content-Length": str(file.stat().st_size)},
                )
                response = connection.getresponse()
                response.read()
            if not 200 <= response.status < 300:
                raise UploadError(f"S3 upload failed (HTTP {response.status}).")
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise UploadError(f"S3 upload failed: {exc}") from exc
        finally:
            connection.close()

    def register(self, key: str, metadata: dict) -> str:
        result = self._post_json("/api/videos/register", {"s3Key": key, **metadata})
        clip_id = result.get("clip", {}).get("id") if isinstance(result.get("clip"), dict) else None
        if not isinstance(clip_id, str):
            raise UploadError("Registration did not return a clip ID.")
        return clip_id
