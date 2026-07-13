import socket
import os
import threading
import json
from typing import Dict, Optional

class PVTelemetryServer:
    """
    UNIX socket server for sending real-time PV (process variable) values to the controller.
    The simulator should call send_pv_values() at each step to update the latest PVs.
    """
    def __init__(self, unix_socket: Optional[str] = None):
        self._latest_pvs: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._unix_socket = unix_socket
        self._server = None
        self._clients = []

    def start(self):
        # UNIX-socket-only server
        if not self._unix_socket:
            raise RuntimeError("PVTelemetryServer requires a unix_socket path")

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Remove leftover socket file if present to avoid "Address already in use"
        try:
            if os.path.exists(self._unix_socket):
                os.unlink(self._unix_socket)
        except Exception:
            pass
        self._server.bind(self._unix_socket)
        try:
            os.chmod(self._unix_socket, 0o777)
        except Exception:
            pass
        self._server.listen(5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop_event.is_set():
            try:
                client, _ = self._server.accept()
                # On new client connect, add to clients list and send the
                # latest PV snapshot immediately if available so clients
                # that connect between simulator steps still receive a
                # recent payload.
                with self._lock:
                    self._clients.append(client)
                    try:
                        if self._latest_pvs:
                            msg = json.dumps(self._latest_pvs).encode("utf-8")
                            client.sendall(msg + b"\n")
                    except Exception:
                        try:
                            client.close()
                        except Exception:
                            pass
                        if client in self._clients:
                            self._clients.remove(client)
            except Exception:
                continue

    def send_pv_values(self, pv_values: Dict[str, dict]):
        """
        Send the latest PV values to all connected clients as a JSON dict.
        """
        with self._lock:
            # Keep a simple name->value view for internal introspection
            self._latest_pvs = {k: v["value"] if isinstance(v, dict) and "value" in v else v for k, v in pv_values.items()}
            payload = pv_values
            for client in self._clients[:]:
                try:
                    msg = json.dumps(payload).encode("utf-8")
                    client.sendall(msg + b"\n")
                except Exception:
                    try:
                        client.close()
                    except Exception:
                        pass
                    self._clients.remove(client)

    def stop(self):
        self._stop_event.set()
        if self._server:
            self._server.close()
        # Clean up unix socket file if we created one
        try:
            if self._unix_socket and os.path.exists(self._unix_socket):
                os.unlink(self._unix_socket)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._lock:
            for client in self._clients:
                try:
                    client.close()
                except Exception:
                    pass
            self._clients.clear()
