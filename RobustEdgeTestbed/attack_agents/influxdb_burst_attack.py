"""
    Agent for network attack from container

    Author: Thankesavan Sivanthi
"""
from attack_agents.attack_agent import AttackAgent
from util.logger_util import LoggerUtil
from simulator.common.config_loader import load_config as _load_config
import importlib

import threading
import time

class InfluxDBBurstAttack(AttackAgent):
    """
    Concrete attack agent for simulating a burst write attack on an InfluxDB container.
    Writes a configurable number of points per second for a fixed duration, simulating a denial-of-service or data flooding event.
    Configuration is loaded from config.json (attack_agent section).
    """
    def __init__(self):
        """
        Initializes the attack agent with configuration parameters from config.json (attack_agent section).
        Raises ValueError if required config is missing.
        """
        self.logger = LoggerUtil.__call__().getLogger()

        # Load configuration from config.json
        try:
            conn = _load_config("influxdb")
            attack_cfg = _load_config("attack_agent")
            self.host = conn.get("host", "localhost")
            self.port = conn.get("port", 8086)
            self.database = conn.get("database")
            self.username = conn.get("username")
            self.password = conn.get("password")
            self.measurement = attack_cfg.get("measurement", "attack_records")
            self.tag = attack_cfg.get("tag", "host")
            self.burst_duration = float(attack_cfg.get("burstDuration", 10))
            self.burst_pps = float(attack_cfg.get("burstPps", 100.0))
            self.burst_value = float(attack_cfg.get("burstValue", 0.0))
            self.url = f"http://{self.host}:{self.port}"
        except ValueError as e:
            self.logger.error(e)


        # Validate required config
        if not self.database:
            raise ValueError("InfluxDBBurstAttack requires 'database' (or legacy 'bucket') in config")


        # Dynamically import InfluxDB client (for v1)
        try:
            InfluxDBClient = importlib.import_module("influxdb").InfluxDBClient
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "InfluxDB v1 mode requires the 'influxdb' Python package. Install it with: pip install influxdb"
            ) from e


        # Initialize InfluxDB client
        self.client = InfluxDBClient(
            host=self.host,
            port=int(self.port),
            username=self.username,
            password=self.password,
            database=self.database,
        )

        # Threading primitives for attack control
        self.stopEvent = threading.Event()
        self.attackThread = threading.Thread(target=self.__runAttack)
       
    def startAttack(self):
        """
        Starts the attack in a background thread.
        """
        self.logger.info("[InfluxDBBurstAttack] Launching container network attack")
        self.attackThread.start()
            
    def stopAttack(self):
        """
        Signals the attack thread to stop and waits for it to finish.
        """
        self.logger.info("[InfluxDBBurstAttack] Stopping container network attack")
        self.stopEvent.set()
        self.attackThread.join()
        # python-influxdb doesn't require an explicit close for HTTP sessions.

    def __runAttack(self):
        """
        Runs the attack until stopAttack is called.
        Sends a fixed value at the configured rate for the configured duration.
        """
        try:
            pps = max(0.0001, float(self.burst_pps))  # Protect against zero or negative pps
            interval = 1.0 / pps
            steps = max(1, int(round(self.burst_duration * pps)))
            for _ in range(steps):
                if self.stopEvent.is_set():
                    break
                v = self.burst_value
                # Construct InfluxDB point
                point = {
                    "measurement": self.measurement,
                    "tags": {"paramId": self.tag, "unit": "kW"},
                    "time": time.time_ns(),
                    "fields": {"value": float(v)},
                }
                self.client.write_points([point], time_precision="n")
                self.logger.info(f"[InfluxDBBurstAttack][burst] wrote value={v}")
                time.sleep(interval)
        except Exception as e:
            self.logger.error(f"[InfluxDBBurstAttack] Error running burst: {e}")