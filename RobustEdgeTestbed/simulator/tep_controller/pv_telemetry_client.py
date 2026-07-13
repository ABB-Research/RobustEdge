import socket
import json
import os
from typing import Dict, Optional


# Module-level persistent socket and path so callers can reuse connection
_sock: Optional[socket.socket] = None
_sock_path: Optional[str] = None


def _close_sock() -> None:
    global _sock, _sock_path
    try:
        if _sock:
            _sock.close()
    except Exception:
        pass
    _sock = None
    _sock_path = None


def _ensure_connected(unix_socket: str, timeout: float) -> bool:
    """Ensure module-level socket is connected to unix_socket.

    Returns True if connected, False otherwise.
    """
    global _sock, _sock_path
    if not unix_socket or not os.path.exists(unix_socket):
        _close_sock()
        return False
    # If already connected to same path, just return True
    if _sock is not None and _sock_path == unix_socket:
        return True
    # Otherwise (re)connect
    _close_sock()
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(unix_socket)
        # keep the timeout configurable per-recv; set a reasonable default
        s.settimeout(timeout)
        _sock = s
        _sock_path = unix_socket
        return True
    except Exception:
        _close_sock()
        return False


def get_latest_pv_values(unix_socket: str = "/sockets/pv.sock", timeout: float = 2.0) -> Dict[str, dict]:
    """Return latest PV values using a persistent connection.

    On first call this will connect and on subsequent calls will reuse the
    same socket. If the socket is closed by the server or an error occurs,
    this will attempt to reconnect on the next call and return an empty
    dict for the current call.
    """
    global _sock
    if not _ensure_connected(unix_socket, timeout):
        return {}

    s = _sock
    if s is None:
        return {}

    # Ensure recv uses provided timeout
    try:
        s.settimeout(timeout)
    except Exception:
        pass

    data = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                # socket closed by peer
                _close_sock()
                return {}
            data += chunk
            if b"\n" in chunk:
                break
    except socket.timeout:
        # No data available within timeout — treat as transient
        return {}
    except (ConnectionResetError, BrokenPipeError, OSError):
        _close_sock()
        return {}

    if not data:
        return {}
    line = data.split(b"\n")[0]
    try:
        payload = json.loads(line.decode("utf-8"))
        return payload
    except Exception:
        return {}
