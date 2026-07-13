import socket
import json
import os
from typing import Dict, Optional


def send_mv_commands(commands: Dict[str, float], unix_socket: Optional[str] = "/sockets/mv.sock", timeout: float = 2.0):
    """Send MV commands to the simulator via UNIX socket only.

    Raises RuntimeError if the UNIX socket path is not provided or does not exist.
    """
    if not unix_socket or not os.path.exists(unix_socket):
        raise RuntimeError(f"UNIX socket not available: {unix_socket}")
    data = json.dumps(commands).encode('utf-8')
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(unix_socket)
    try:
        s.sendall(data)
    finally:
        try:
            s.close()
        except Exception:
            pass
