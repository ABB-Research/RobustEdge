import socket
import os
import threading
import json
from typing import Dict, Optional

class MVCommandServer:
    """
    UNIX socket server for receiving real-time MV (manipulated variable) commands from the controller.
    Stores the latest command set received and provides thread-safe access for the simulator.
    """
    def __init__(self, host: str = 'localhost', port: int = 50051, unix_socket: Optional[str] = None):
        # Latest MV commands received (thread-safe)
        self._latest_commands: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._host = host
        self._port = port
        self._unix_socket = unix_socket
        self._server = None
        

    def start(self):
        """
        Starts the MV command server in a background thread.
        UNIX-socket-only server; raises if no unix_socket provided.
        """
        if not self._unix_socket:
            raise RuntimeError("MVCommandServer requires a unix_socket path")

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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
        """
        Main server loop: accepts connections and updates latest MV commands.
        Each connection is expected to send a JSON dict of MV values.
        """
        while not self._stop_event.is_set():
            try:
                client, _ = self._server.accept()
                data = client.recv(4096)
                if data:
                    try:
                        commands = json.loads(data.decode('utf-8'))
                        if isinstance(commands, dict):
                            with self._lock:
                                self._latest_commands = {k: float(v) for k, v in commands.items()}
                    except Exception:
                        pass  # Ignore malformed input
                client.close()
            except Exception:
                continue

    def get_latest_commands(self) -> Dict[str, float]:
        """
        Returns the most recently received MV commands as a dict.
        Thread-safe for concurrent access by simulator.
        """
        with self._lock:
            return dict(self._latest_commands)

    def stop(self):
        """
        Stops the server and waits for the background thread to exit.
        """
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
