"""Talk to the signed-out Chrome on 127.0.0.1. No third-party packages.

The socket speaks only to a debugger URL on this machine. Page commands go to
the window this program launched, not to any other Chrome.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class CdpError(RuntimeError):
    """The signed-out Chrome's debugger refused a command or closed."""


def list_pages(port: int, timeout: float = 2) -> list[dict]:
    payload = _get_json(port, "/json/list", timeout)
    if not isinstance(payload, list):
        raise CdpError("debugger page list was not a list")
    pages = []
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "page":
            continue
        socket_url = item.get("webSocketDebuggerUrl") or ""
        if _local_ws(socket_url):
            pages.append(item)
    return pages


def open_page(port: int, url: str, timeout: float = 5) -> dict:
    quoted = urllib.parse.quote(url, safe=":/?&=%")
    payload = _get_json(port, f"/json/new?{quoted}", timeout)
    if not isinstance(payload, dict):
        raise CdpError("debugger did not return a page")
    return payload


def activate_page(port: int, target_id: str, timeout: float = 5) -> None:
    _require_target(target_id)
    _get_text(port, f"/json/activate/{target_id}", timeout)


def close_page(port: int, target_id: str, timeout: float = 5) -> None:
    _require_target(target_id)
    _get_text(port, f"/json/close/{target_id}", timeout)


def call(ws_url: str, method: str, params: dict | None = None, timeout: float = 5) -> dict:
    """Send one DevTools command and return its result object."""

    if not _local_ws(ws_url):
        raise CdpError("refusing a debugger outside this machine")
    if not method or not method.replace(".", "").isalnum():
        raise CdpError("refusing debugger method")
    link = _connect(ws_url, timeout)
    try:
        message = {"id": 1, "method": method, "params": params or {}}
        _send_text(link, json.dumps(message))
        for _ in range(40):
            payload = _read_text(link)
            if payload is None:
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise CdpError("debugger returned junk") from exc
            if data.get("id") != 1:
                continue
            if "error" in data:
                raise CdpError(str(data["error"]))
            result = data.get("result")
            return result if isinstance(result, dict) else {}
        raise CdpError("debugger did not answer")
    finally:
        link.close()


def _require_target(target_id: str) -> None:
    if not target_id or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in target_id):
        raise CdpError("refusing target id")


def _local_ws(ws_url: str) -> bool:
    parts = urllib.parse.urlsplit(ws_url)
    return parts.scheme == "ws" and parts.hostname in {"127.0.0.1", "localhost"} and parts.port is not None


def _check_port(port: int) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or port < 1 or port > 65535:
        raise CdpError("bad debugger port")


def _get_json(port: int, path: str, timeout: float):
    body = _get_text(port, path, timeout)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise CdpError("debugger returned junk") from exc


def _get_text(port: int, path: str, timeout: float) -> str:
    _check_port(port)
    if not path.startswith("/json/"):
        raise CdpError("refusing debugger path")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode()
    except urllib.error.URLError as exc:
        raise CdpError("debugger is not up") from exc


class _Link:
    """A websocket plus any bytes already read past the handshake."""

    def __init__(self, sock: socket.socket, pending: bytes = b"") -> None:
        self.sock = sock
        self.pending = bytearray(pending)

    def close(self) -> None:
        self.sock.close()

    def sendall(self, data: bytes) -> None:
        self.sock.sendall(data)


def _connect(ws_url: str, timeout: float) -> _Link:
    parts = urllib.parse.urlsplit(ws_url)
    host = parts.hostname or ""
    port = parts.port or 0
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    sock = socket.create_connection((host, port), timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Origin: http://127.0.0.1:{port}\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode())
    link = _Link(sock)
    header = _read_until(link, b"\r\n\r\n")
    head = header[:-4]
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
        raise CdpError("debugger refused the connection")
    expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    accept = ""
    for line in head.decode(errors="replace").split("\r\n"):
        if line.lower().startswith("sec-websocket-accept:"):
            accept = line.split(":", 1)[1].strip()
    if accept != expected:
        raise CdpError("debugger failed the handshake")
    return link


def _read_until(link: _Link, marker: bytes) -> bytes:
    data = link.pending
    while marker not in data:
        chunk = link.sock.recv(4096)
        if not chunk:
            raise CdpError("debugger closed")
        data.extend(chunk)
        if len(data) > 65536:
            raise CdpError("debugger header is too large")
    end = data.index(marker) + len(marker)
    header = bytes(data[:end])
    del data[:end]
    return header


def _send_text(link: _Link, text: str) -> None:
    payload = text.encode()
    mask = os.urandom(4)
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(length.to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(length.to_bytes(8, "big"))
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    link.sendall(bytes(header) + masked)


def _read_text(link: _Link) -> str | None:
    fragments = bytearray()
    while True:
        fin, opcode, payload = _read_frame(link)
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            _send_control(link, 0xA, payload)
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x1:
            fragments = bytearray(payload)
        elif opcode == 0x0:
            fragments.extend(payload)
        else:
            raise CdpError("debugger sent a binary frame")
        if fin:
            return fragments.decode()


def _send_control(link: _Link, opcode: int, payload: bytes) -> None:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode, 0x80 | len(payload)])
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    link.sendall(bytes(header) + masked)


def _read_frame(link: _Link) -> tuple[bool, int, bytes]:
    header = _recv_exact(link, 2)
    fin = bool(header[0] & 0x80)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(_recv_exact(link, 2), "big")
    elif length == 127:
        length = int.from_bytes(_recv_exact(link, 8), "big")
    if length > 1_000_000:
        raise CdpError("debugger frame is too large")
    if header[1] & 0x80:
        mask = _recv_exact(link, 4)
        raw = _recv_exact(link, length)
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
    else:
        payload = _recv_exact(link, length)
    return fin, opcode, payload


def _recv_exact(link: _Link, size: int) -> bytes:
    while len(link.pending) < size:
        chunk = link.sock.recv(max(4096, size - len(link.pending)))
        if not chunk:
            raise CdpError("debugger closed")
        link.pending.extend(chunk)
    data = bytes(link.pending[:size])
    del link.pending[:size]
    return data
