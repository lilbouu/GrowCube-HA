"""GrowCube firmware update helpers."""

from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

FIRMWARE_UPDATE_CHECK_URL = "https://www.growcube.cc/software/2.4G/"
FIRMWARE_LATEST_MESSAGE = "当前已是最新版本！"
FIRMWARE_DOWNLOAD_TIMEOUT_SECONDS = 60
FIRMWARE_UPLOAD_TIMEOUT_SECONDS = 120
FIRMWARE_MAX_BYTES = 4 * 1024 * 1024


def check_growcube_firmware_update(current_version: str | None) -> dict[str, Any]:
    """Check GrowCube's firmware server for the firmware advertised to Android."""

    version = normalize_firmware_version(current_version)
    query_url = f"{FIRMWARE_UPDATE_CHECK_URL}?v={quote(version)}"
    request = Request(query_url, headers={"User-Agent": "GrowCubeHACS/2.3"}, method="GET")
    try:
        with urlopen(request, timeout=FIRMWARE_DOWNLOAD_TIMEOUT_SECONDS) as response:
            line = response.readline(2048).decode("utf-8", errors="replace").strip()
    except HTTPError as err:
        body_text = err.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"firmware check failed: HTTP {err.code}: {body_text[:160]}") from err
    except URLError as err:
        raise RuntimeError(f"firmware check failed: {err.reason}") from err

    if not line:
        raise RuntimeError("firmware check failed: empty server response")
    if line == FIRMWARE_LATEST_MESSAGE:
        return {
            "update_available": False,
            "current_version": current_version or "",
            "latest_version": current_version or "",
            "download_url": "",
            "message": "latest installed",
        }

    download_url = urljoin(FIRMWARE_UPDATE_CHECK_URL, line)
    parsed = urlparse(download_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path.lower().endswith(".bin"):
        raise RuntimeError(f"firmware check failed: unexpected server response: {line[:160]}")

    return {
        "update_available": True,
        "current_version": current_version or "",
        "latest_version": firmware_version_from_url(download_url) or "",
        "download_url": download_url,
        "message": "update available",
    }


def download_growcube_firmware_update(current_version: str | None) -> Path:
    """Download the firmware image advertised by the GrowCube firmware server."""

    info = check_growcube_firmware_update(current_version)
    if not info.get("update_available"):
        raise RuntimeError("device firmware is already up to date")

    download_url = str(info.get("download_url") or "")
    request = Request(download_url, headers={"User-Agent": "GrowCubeHACS/2.3"}, method="GET")
    try:
        with urlopen(request, timeout=FIRMWARE_DOWNLOAD_TIMEOUT_SECONDS) as response:
            body = response.read(FIRMWARE_MAX_BYTES + 1)
    except HTTPError as err:
        body_text = err.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"firmware download failed: HTTP {err.code}: {body_text[:160]}") from err
    except URLError as err:
        raise RuntimeError(f"firmware download failed: {err.reason}") from err

    if len(body) > FIRMWARE_MAX_BYTES:
        raise RuntimeError(f"firmware image is too large: {len(body)} bytes")
    if not body:
        raise RuntimeError("firmware download failed: empty file")

    path = Path(tempfile.gettempdir()) / f"growcube-firmware-{uuid.uuid4().hex}.bin"
    path.write_bytes(body)
    return validate_firmware_image(path)


def upload_firmware_image(host: str, path: Path) -> dict[str, Any]:
    """Upload a firmware image to GrowCube's ElegantOTA endpoint."""

    firmware = validate_firmware_image(path)
    boundary = f"----GrowCubeFirmware{uuid.uuid4().hex}"
    filename = "GrowCube-Software.bin"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n"
        "\r\n"
    ).encode("ascii")
    footer = f"\r\n--{boundary}--\r\n".encode("ascii")
    body = header + firmware.read_bytes() + footer
    request = Request(
        f"http://{http_host(host)}/update",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data;boundary={boundary}",
            "Content-Length": str(len(body)),
            "Connection": "close",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=FIRMWARE_UPLOAD_TIMEOUT_SECONDS) as response:
            response_body = response.read(4096).decode("utf-8", errors="replace")
            return {
                "status": int(response.status),
                "firmware": firmware.name,
                "bytes": firmware.stat().st_size,
                "response": response_body[:240],
            }
    except HTTPError as err:
        body_text = err.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"firmware upload failed: HTTP {err.code}: {body_text[:240]}") from err
    except URLError as err:
        raise RuntimeError(f"firmware upload failed: {err.reason}") from err


def validate_firmware_image(path: Path) -> Path:
    """Validate the basic shape of a firmware image."""

    if not path.is_file():
        raise RuntimeError(f"firmware image not found: {path}")
    if path.suffix.lower() != ".bin":
        raise RuntimeError(f"firmware image must be a .bin file: {path.name}")
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError(f"firmware image is empty: {path.name}")
    if size > FIRMWARE_MAX_BYTES:
        raise RuntimeError(f"firmware image is too large: {size} bytes")
    return path


def normalize_firmware_version(version: str | None) -> str:
    """Normalize empty firmware versions for GrowCube's update API."""

    text = str(version or "").strip()
    return text if text else "0"


def firmware_version_from_url(url: str) -> str | None:
    """Extract a version from GrowCube firmware file names."""

    filename = Path(urlparse(url).path).name
    match = re.search(r"(?:^|_)V(\d+(?:\.\d+)*)(?:_|\.bin$)", filename, flags=re.IGNORECASE)
    return match.group(1) if match else None


def http_host(host: str) -> str:
    """Return a URL-safe HTTP host string."""

    text = str(host or "").strip()
    if ":" in text and not text.startswith("["):
        return f"[{text}]"
    return text
