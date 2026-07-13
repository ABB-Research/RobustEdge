from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable, Optional, TextIO


class BaseSink:
    def emit_measurement(self, record: dict) -> None:
        raise NotImplementedError

    def emit_event(self, record: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class JsonlSink(BaseSink):
    def __init__(self, measurement_path: Optional[str] = "-", event_path: Optional[str] = None):
        self.measurement_stream = self._open_path(measurement_path)
        if event_path is None:
            self.event_stream = self.measurement_stream
        else:
            self.event_stream = self._open_path(event_path)

    def _open_path(self, path: Optional[str]) -> TextIO:
        if path in {None, "-"}:
            return sys.stdout
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return out_path.open("w", encoding="utf-8")

    def emit_measurement(self, record: dict) -> None:
        self.measurement_stream.write(json.dumps(record) + "\n")

    def emit_event(self, record: dict) -> None:
        self.event_stream.write(json.dumps(record) + "\n")

    def close(self) -> None:
        if self.measurement_stream is not sys.stdout:
            self.measurement_stream.close()
        if self.event_stream not in {self.measurement_stream, sys.stdout}:
            self.event_stream.close()


class InfluxDBSink(BaseSink):
    def __init__(
        self,
        host: str = "localhost",
        port: int = 8086,
        username: str = "admin",
        password: str = "change_me_admin_password",
        database: str = "appdb",
        measurement_name: str = "tep_signals",
        event_measurement_name: str = "tep_alarm_events",
        ssl: bool = False,
    ) -> None:
        try:
            from influxdb import InfluxDBClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("InfluxDB output mode requires the 'influxdb' package.") from exc

        self.client = InfluxDBClient(
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            ssl=ssl,
        )
        self.measurement_name = measurement_name
        self.event_measurement_name = event_measurement_name
        self.client.create_database(database)
        self.client.switch_database(database)

    def _base_point(self, record: dict, measurement: str) -> dict:
        timestamp_ns = int(float(record["timestamp"]) * 1e9)
        return {
            "measurement": measurement,
            "time": timestamp_ns,
            "tags": {
                "record_type": record.get("record_type", "unknown"),
                "name": record.get("name", "unknown"),
            },
            "fields": {},
        }

    def emit_measurement(self, record: dict) -> None:
        point = self._base_point(record, self.measurement_name)
        tags = point["tags"]
        fields = point["fields"]
        if record["record_type"] == "pv":
            tags["category"] = record.get("category", "unknown")
            tags["unit"] = record.get("unit", "")
            fields["value"] = float(record["value"])
        elif record["record_type"] == "mv":
            tags["description"] = record.get("description", "")
            fields["command"] = float(record["command"])
            fields["feedback"] = float(record["feedback"])
        elif record["record_type"] == "alarm_state":
            tags["source"] = record.get("source", "")
            fields["state"] = record["state"]
            fields["severity"] = int(record["severity"])
        else:
            for key, value in record.items():
                if key in {"timestamp", "record_type", "name"}:
                    continue
                if isinstance(value, (int, float, bool, str)):
                    fields[key] = value
        self.client.write_points([point], time_precision="n")

    def emit_event(self, record: dict) -> None:
        point = self._base_point(record, self.event_measurement_name)
        point["fields"] = {
            "previous_state": record.get("previous_state", "UNKNOWN"),
            "state": record.get("state", "UNKNOWN"),
        }
        self.client.write_points([point], time_precision="n")

    def close(self) -> None:
        self.client.close()


class MultiSink(BaseSink):
    def __init__(self, sinks: Iterable[BaseSink]):
        self.sinks = list(sinks)

    def emit_measurement(self, record: dict) -> None:
        for sink in self.sinks:
            sink.emit_measurement(record)

    def emit_event(self, record: dict) -> None:
        for sink in self.sinks:
            sink.emit_event(record)

    def close(self) -> None:
        for sink in self.sinks:
            sink.close()
