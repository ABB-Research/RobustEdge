from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Protocol

from simulator.tep_process.config import (
    ALARM_SEVERITY,
    MVSpec,
    RateConfig,
    VariableSpec,
    build_default_hidden_states,
    build_default_mv_specs,
    build_default_pv_specs,
)


class SinkProtocol(Protocol):
    def emit_measurement(self, record: dict) -> None:
        ...

    def emit_event(self, record: dict) -> None:
        ...


@dataclass
class VariableRuntime:
    true_value: float
    measured_value: float
    visible_value: float
    next_emit_at: float
    next_sample_at: float
    pending_value: Optional[float] = None
    pending_release_at: Optional[float] = None
    alarm_state: str = "NORMAL"


@dataclass
class MVRuntime:
    command: float
    feedback: float
    next_emit_at: float
    alarm_state: str = "NORMAL"


class TEPSimulator:
    def __init__(self, rates: RateConfig, seed: int = 10871):
        self.rates = rates
        self.random = random.Random(seed)
        self.base_step = 1.0
        self.sim_time = time.time()

        self.pv_specs = build_default_pv_specs(rates, self.random)
        self.pv_by_name = {spec.name: spec for spec in self.pv_specs}
        self.mv_specs = build_default_mv_specs(rates)
        self.mv_by_name = {spec.name: spec for spec in self.mv_specs}

        self.hidden_states = build_default_hidden_states()
        self.pv_runtime = self._build_pv_runtime()
        self.mv_runtime = self._build_mv_runtime()

    def _build_pv_runtime(self) -> Dict[str, VariableRuntime]:
        runtime: Dict[str, VariableRuntime] = {}
        for spec in self.pv_specs:
            runtime[spec.name] = VariableRuntime(
                true_value=spec.nominal,
                measured_value=spec.nominal,
                visible_value=spec.nominal,
                next_emit_at=0.0 if spec.category != "composition" else spec.dead_time,
                next_sample_at=0.0,
                alarm_state="NORMAL",
            )
        return runtime

    def _build_mv_runtime(self) -> Dict[str, MVRuntime]:
        return {
            spec.name: MVRuntime(command=spec.command, feedback=spec.feedback, next_emit_at=0.0, alarm_state="NORMAL")
            for spec in self.mv_specs
        }

    def _clamp(self, value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    def _gauss(self, sigma: float) -> float:
        return self.random.gauss(0.0, sigma)

    def _update_mv(self, dt: float, commands: Dict[str, float]) -> None:
        for mv_spec in self.mv_specs:
            mv_state = self.mv_runtime[mv_spec.name]
            if mv_spec.name in commands:
                mv_state.command = self._clamp(commands[mv_spec.name], mv_spec.min_value, mv_spec.max_value)
            # If no command for this MV, hold the last command (no drift).
            delta = mv_state.command - mv_state.feedback
            max_step = mv_spec.rate_limit * dt
            delta = self._clamp(delta, -max_step, max_step)
            mv_state.feedback = self._clamp(mv_state.feedback + delta, mv_spec.min_value, mv_spec.max_value)

    def _update_hidden_states(self, dt: float) -> None:
        # Phenomenological process model loosely inspired by the Tennessee Eastman
        # challenge problem (Downs & Vogel, 1993, Comput. Chem. Eng. 17(3) pp.245-255).
        # Hidden states evolve as first-order lags driven by MV feedback positions.
        # Observed PVs are derived from these hidden states with added noise and dead-time.
        m = {name: self.mv_runtime[name].feedback / 100.0 for name in self.mv_runtime}
        feed_total_target = 1.2 + 2.6 * (m["mv_01_a_feed_valve"] + m["mv_02_d_feed_valve"] + m["mv_03_e_feed_valve"]) / 1.6
        recycle_target = 1.0 + 3.0 * m["mv_04_recycle_valve"]
        purge_target = 0.6 + 3.0 * m["mv_05_purge_valve"]
        coolant_target = 0.9 + 2.6 * m["mv_06_reactor_coolant_valve"]
        sep_dump_target = 0.8 + 2.5 * m["mv_07_separator_dump_valve"]
        steam_target = 0.7 + 2.0 * m["mv_08_stripper_steam_valve"]
        product_target = 0.9 + 2.5 * m["mv_09_product_draw_valve"]
        compressor_target = 1.1 + 2.8 * m["mv_10_compressor_speed"]
        condenser_target = 0.8 + 2.0 * m["mv_11_condenser_cooling_valve"]
        analyzer_bias = m["mv_12_analyzer_selector"]

        def track(key: str, target: float, tau: float, noise: float) -> None:
            current = self.hidden_states[key]
            self.hidden_states[key] = current + (target - current) * dt / tau + self._gauss(noise)

        track("feed_total", feed_total_target, 10.0, 0.02)
        track("recycle_flow", recycle_target, 12.0, 0.02)
        track("purge_flow", purge_target, 10.0, 0.01)
        track("steam_flow", steam_target, 14.0, 0.02)
        track("cooling_flow", coolant_target, 10.0, 0.02)
        track("condenser_flow", condenser_target, 12.0, 0.02)
        track("product_flow", product_target, 14.0, 0.02)
        track("compressor_flow", compressor_target, 12.0, 0.03)

        self.hidden_states["sep_liquid_flow"] = 0.5 * self.hidden_states["feed_total"] + 0.4 * self.hidden_states["recycle_flow"] + 0.2 * sep_dump_target + self._gauss(0.02)
        self.hidden_states["vent_flow"] = 0.15 * self.hidden_states["purge_flow"] + 0.05 * self.hidden_states["compressor_flow"] + self._gauss(0.01)
        self.hidden_states["aux_flow"] = 0.7 + 0.15 * analyzer_bias + self._gauss(0.01)
        self.hidden_states["product_recycle_flow"] = 0.35 * self.hidden_states["product_flow"] + 0.25 * self.hidden_states["recycle_flow"] + self._gauss(0.01)

        reaction_drive = self.hidden_states["feed_total"] * 12.0 + self.hidden_states["steam_flow"] * 7.0 - self.hidden_states["cooling_flow"] * 10.0
        self.hidden_states["reactor_pressure"] += ((2400.0 + 130.0 * self.hidden_states["feed_total"] + 90.0 * self.hidden_states["compressor_flow"] - 110.0 * self.hidden_states["purge_flow"]) - self.hidden_states["reactor_pressure"]) * dt / 25.0 + self._gauss(1.0)
        self.hidden_states["separator_pressure"] += ((2100.0 + 80.0 * self.hidden_states["recycle_flow"] - 70.0 * self.hidden_states["condenser_flow"]) - self.hidden_states["separator_pressure"]) * dt / 30.0 + self._gauss(0.9)
        self.hidden_states["stripper_pressure"] += ((1900.0 + 75.0 * self.hidden_states["steam_flow"] - 45.0 * self.hidden_states["product_flow"]) - self.hidden_states["stripper_pressure"]) * dt / 35.0 + self._gauss(0.8)
        self.hidden_states["compressor_pressure"] += ((2500.0 + 140.0 * self.hidden_states["compressor_flow"] - 80.0 * self.hidden_states["condenser_flow"]) - self.hidden_states["compressor_pressure"]) * dt / 22.0 + self._gauss(1.1)
        self.hidden_states["purge_header_pressure"] += ((700.0 + 50.0 * self.hidden_states["reactor_pressure"] / 1000.0) - self.hidden_states["purge_header_pressure"]) * dt / 28.0 + self._gauss(0.6)
        self.hidden_states["condenser_pressure"] += ((650.0 + 45.0 * self.hidden_states["separator_pressure"] / 1000.0 - 35.0 * self.hidden_states["condenser_flow"]) - self.hidden_states["condenser_pressure"]) * dt / 30.0 + self._gauss(0.5)
        self.hidden_states["feed_header_pressure"] += ((1200.0 + 60.0 * self.hidden_states["feed_total"]) - self.hidden_states["feed_header_pressure"]) * dt / 20.0 + self._gauss(0.6)
        self.hidden_states["steam_header_pressure"] += ((550.0 + 45.0 * self.hidden_states["steam_flow"]) - self.hidden_states["steam_header_pressure"]) * dt / 18.0 + self._gauss(0.4)
        self.hidden_states["analyzer_pressure"] += ((250.0 + 20.0 * analyzer_bias) - self.hidden_states["analyzer_pressure"]) * dt / 16.0 + self._gauss(0.3)
        self.hidden_states["reactor_dp"] += ((180.0 + 18.0 * self.hidden_states["feed_total"] + 8.0 * self.hidden_states["recycle_flow"]) - self.hidden_states["reactor_dp"]) * dt / 18.0 + self._gauss(0.4)
        self.hidden_states["separator_dp"] += ((150.0 + 10.0 * self.hidden_states["sep_liquid_flow"]) - self.hidden_states["separator_dp"]) * dt / 20.0 + self._gauss(0.4)
        self.hidden_states["column_top_pressure"] += ((720.0 + 15.0 * self.hidden_states["steam_flow"] - 12.0 * self.hidden_states["condenser_flow"]) - self.hidden_states["column_top_pressure"]) * dt / 26.0 + self._gauss(0.4)

        self.hidden_states["reactor_temperature"] += ((108.0 + reaction_drive) - self.hidden_states["reactor_temperature"]) * dt / 40.0 + self._gauss(0.08)
        self.hidden_states["separator_temperature"] += ((74.0 + 0.18 * self.hidden_states["reactor_temperature"] - 3.5 * self.hidden_states["condenser_flow"]) - self.hidden_states["separator_temperature"]) * dt / 45.0 + self._gauss(0.05)
        self.hidden_states["stripper_temperature"] += ((96.0 + 8.5 * self.hidden_states["steam_flow"] - 0.1 * self.hidden_states["product_flow"]) - self.hidden_states["stripper_temperature"]) * dt / 55.0 + self._gauss(0.06)
        self.hidden_states["condenser_temperature"] += ((36.0 + 0.08 * self.hidden_states["separator_pressure"] / 10.0 - 5.5 * self.hidden_states["condenser_flow"]) - self.hidden_states["condenser_temperature"]) * dt / 25.0 + self._gauss(0.05)
        self.hidden_states["feed_temperature"] += ((28.0 + 2.0 * self.hidden_states["feed_total"] / 10.0) - self.hidden_states["feed_temperature"]) * dt / 60.0 + self._gauss(0.03)
        self.hidden_states["compressor_temperature"] += ((60.0 + 7.0 * self.hidden_states["compressor_flow"] + 0.02 * self.hidden_states["compressor_pressure"] / 10.0) - self.hidden_states["compressor_temperature"]) * dt / 25.0 + self._gauss(0.05)
        self.hidden_states["cooling_water_temperature"] += ((23.0 + 0.25 * self.hidden_states["reactor_temperature"] / 10.0) - self.hidden_states["cooling_water_temperature"]) * dt / 35.0 + self._gauss(0.03)
        self.hidden_states["steam_temperature"] += ((145.0 + 4.0 * self.hidden_states["steam_flow"]) - self.hidden_states["steam_temperature"]) * dt / 30.0 + self._gauss(0.05)
        self.hidden_states["product_temperature"] += ((58.0 + 0.25 * self.hidden_states["stripper_temperature"]) - self.hidden_states["product_temperature"]) * dt / 40.0 + self._gauss(0.04)
        self.hidden_states["purge_temperature"] += ((38.0 + 0.18 * self.hidden_states["reactor_temperature"]) - self.hidden_states["purge_temperature"]) * dt / 35.0 + self._gauss(0.04)
        self.hidden_states["recycle_temperature"] += ((50.0 + 0.22 * self.hidden_states["separator_temperature"]) - self.hidden_states["recycle_temperature"]) * dt / 38.0 + self._gauss(0.04)
        self.hidden_states["ambient_temperature"] += (23.0 - self.hidden_states["ambient_temperature"]) * dt / 400.0 + self._gauss(0.01)
        self.hidden_states["column_bottom_temperature"] += ((128.0 + 0.3 * self.hidden_states["steam_flow"] * 10.0) - self.hidden_states["column_bottom_temperature"]) * dt / 50.0 + self._gauss(0.05)
        self.hidden_states["column_top_temperature"] += ((64.0 + 0.12 * self.hidden_states["column_top_pressure"] / 10.0 - 0.08 * self.hidden_states["condenser_flow"] * 10.0) - self.hidden_states["column_top_temperature"]) * dt / 45.0 + self._gauss(0.04)

        self.hidden_states["separator_level"] += ((52.0 + 6.0 * self.hidden_states["feed_total"] / 10.0 + 3.0 * self.hidden_states["recycle_flow"] / 10.0 - 7.0 * sep_dump_target / 10.0) - self.hidden_states["separator_level"]) * dt / 50.0 + self._gauss(0.05)
        self.hidden_states["stripper_level"] += ((47.0 + 4.0 * sep_dump_target / 10.0 - 6.0 * product_target / 10.0) - self.hidden_states["stripper_level"]) * dt / 55.0 + self._gauss(0.05)
        self.hidden_states["reflux_drum_level"] += ((55.0 + 2.0 * self.hidden_states["condenser_flow"] - 0.08 * self.hidden_states["product_flow"]) - self.hidden_states["reflux_drum_level"]) * dt / 60.0 + self._gauss(0.04)
        self.hidden_states["condensate_level"] += ((43.0 + 0.15 * self.hidden_states["condenser_flow"] * 10.0) - self.hidden_states["condensate_level"]) * dt / 65.0 + self._gauss(0.04)
        self.hidden_states["feed_tank_level"] += ((64.0 - 0.1 * self.hidden_states["feed_total"] * 10.0) - self.hidden_states["feed_tank_level"]) * dt / 120.0 + self._gauss(0.03)
        self.hidden_states["product_tank_level"] += ((58.0 + 0.12 * self.hidden_states["product_flow"] * 10.0 - 0.09 * self.hidden_states["product_recycle_flow"] * 10.0) - self.hidden_states["product_tank_level"]) * dt / 90.0 + self._gauss(0.03)
        self.hidden_states["utility_level"] += (72.0 - self.hidden_states["utility_level"]) * dt / 300.0 + self._gauss(0.02)
        self.hidden_states["wastewater_level"] += ((35.0 + 0.1 * sep_dump_target * 10.0) - self.hidden_states["wastewater_level"]) * dt / 140.0 + self._gauss(0.03)

        quality_shift = 0.25 * analyzer_bias + 0.12 * self.hidden_states["stripper_temperature"] / 100.0 - 0.18 * self.hidden_states["purge_flow"] / 10.0
        self.hidden_states["comp_reactor_a"] += ((34.0 - 4.0 * self.hidden_states["feed_total"] / 10.0 + 2.0 * analyzer_bias) - self.hidden_states["comp_reactor_a"]) * dt / 120.0 + self._gauss(0.02)
        self.hidden_states["comp_reactor_b"] += ((29.0 + quality_shift * 2.0) - self.hidden_states["comp_reactor_b"]) * dt / 120.0 + self._gauss(0.02)
        self.hidden_states["comp_reactor_c"] += ((18.0 + 0.2 * self.hidden_states["reactor_temperature"] / 10.0 - analyzer_bias) - self.hidden_states["comp_reactor_c"]) * dt / 120.0 + self._gauss(0.02)
        self.hidden_states["comp_separator_a"] += ((26.0 - 0.8 * sep_dump_target) - self.hidden_states["comp_separator_a"]) * dt / 150.0 + self._gauss(0.02)
        self.hidden_states["comp_separator_b"] += ((31.0 + 0.9 * analyzer_bias) - self.hidden_states["comp_separator_b"]) * dt / 150.0 + self._gauss(0.02)
        self.hidden_states["comp_separator_c"] += ((22.0 + 0.12 * self.hidden_states["stripper_temperature"] - 0.1 * self.hidden_states["condenser_temperature"]) - self.hidden_states["comp_separator_c"]) * dt / 150.0 + self._gauss(0.02)
        self.hidden_states["comp_inerts"] += ((12.0 + 0.2 * self.hidden_states["purge_flow"] - 0.08 * self.hidden_states["recycle_flow"]) - self.hidden_states["comp_inerts"]) * dt / 180.0 + self._gauss(0.01)
        self.hidden_states["comp_product_g"] += ((74.0 + 0.2 * self.hidden_states["stripper_temperature"] - 0.3 * self.hidden_states["product_flow"] + analyzer_bias * 2.0) - self.hidden_states["comp_product_g"]) * dt / 220.0 + self._gauss(0.015)
        self.hidden_states["comp_product_h"] += ((18.0 - 0.15 * self.hidden_states["stripper_temperature"] + 0.1 * self.hidden_states["product_flow"]) - self.hidden_states["comp_product_h"]) * dt / 220.0 + self._gauss(0.015)
        self.hidden_states["comp_recycle_a"] += ((24.0 + 0.15 * analyzer_bias - 0.08 * self.hidden_states["purge_flow"]) - self.hidden_states["comp_recycle_a"]) * dt / 180.0 + self._gauss(0.015)
        self.hidden_states["comp_recycle_b"] += ((36.0 + 0.2 * self.hidden_states["recycle_flow"] / 10.0) - self.hidden_states["comp_recycle_b"]) * dt / 180.0 + self._gauss(0.015)
        self.hidden_states["comp_offgas_light"] += ((9.0 + 0.1 * self.hidden_states["purge_flow"] - 0.05 * self.hidden_states["condenser_flow"]) - self.hidden_states["comp_offgas_light"]) * dt / 240.0 + self._gauss(0.01)
        self.hidden_states["comp_column_heavy"] += ((81.0 + 0.15 * self.hidden_states["column_bottom_temperature"] / 10.0 - 0.2 * self.hidden_states["product_flow"] / 10.0) - self.hidden_states["comp_column_heavy"]) * dt / 240.0 + self._gauss(0.01)

    def _compute_pv_value(self, spec: VariableSpec) -> float:
        base = self.hidden_states[spec.state_key] * spec.scale + spec.bias + self._gauss(spec.noise)
        return self._clamp(base, spec.min_value, spec.max_value)

    def _alarm_state_for_value(self, value: float, thresholds) -> str:
        if value >= thresholds.hihi:
            return "HIHI"
        if value >= thresholds.hi:
            return "HI"
        if value <= thresholds.lolo:
            return "LOLO"
        if value <= thresholds.lo:
            return "LO"
        return "NORMAL"

    def _emit_mv_updates(self, sink: SinkProtocol) -> None:
        for mv_spec in self.mv_specs:
            mv_state = self.mv_runtime[mv_spec.name]
            if self.sim_time + 1e-9 < mv_state.next_emit_at:
                continue
            mv_state.next_emit_at += mv_spec.sample_period

            previous_alarm = mv_state.alarm_state
            if mv_spec.thresholds is not None:
                alarm_value = mv_state.feedback if mv_spec.alarm_source == "feedback" else mv_state.command
                mv_state.alarm_state = self._alarm_state_for_value(alarm_value, mv_spec.thresholds)

            sink.emit_measurement(
                {
                    "timestamp": round(self.sim_time, 3),
                    "record_type": "mv",
                    "name": mv_spec.name,
                    "description": mv_spec.description,
                    "command": round(mv_state.command, 4),
                    "feedback": round(mv_state.feedback, 4),
                    "unit": "%",
                }
            )

            if mv_spec.thresholds is not None and previous_alarm != mv_state.alarm_state:
                sink.emit_event(
                    {
                        "timestamp": round(self.sim_time, 3),
                        "record_type": "alarm_event",
                        "name": mv_spec.name,
                        "source_type": "mv",
                        "previous_state": previous_alarm,
                        "state": mv_state.alarm_state,
                    }
                )

    def _emit_pv_updates(self, sink: SinkProtocol) -> None:
        for spec in self.pv_specs:
            runtime = self.pv_runtime[spec.name]
            runtime.true_value = self._compute_pv_value(spec)

            if spec.category == "composition":
                if self.sim_time + 1e-9 >= runtime.next_sample_at:
                    runtime.pending_value = runtime.true_value
                    runtime.pending_release_at = self.sim_time + spec.dead_time
                    runtime.next_sample_at += spec.sample_period
                if runtime.pending_release_at is not None and self.sim_time + 1e-9 >= runtime.pending_release_at:
                    runtime.visible_value = runtime.pending_value if runtime.pending_value is not None else runtime.visible_value
                    runtime.measured_value = runtime.visible_value
                    runtime.pending_value = None
                    runtime.pending_release_at = None
                should_emit = self.sim_time + 1e-9 >= runtime.next_emit_at
                if should_emit:
                    runtime.next_emit_at += spec.sample_period
                if not should_emit:
                    continue
            else:
                if self.sim_time + 1e-9 < runtime.next_emit_at:
                    continue
                runtime.next_emit_at += spec.sample_period
                runtime.visible_value = runtime.true_value
                runtime.measured_value = runtime.true_value

            previous_alarm = runtime.alarm_state
            runtime.alarm_state = self._alarm_state_for_value(runtime.measured_value, spec.thresholds)

            sink.emit_measurement(
                {
                    "timestamp": round(self.sim_time, 3),
                    "record_type": "pv",
                    "name": spec.name,
                    "category": spec.category,
                    "value": round(runtime.measured_value, 5),
                    "unit": spec.unit,
                }
            )

            if previous_alarm != runtime.alarm_state:
                sink.emit_event(
                    {
                        "timestamp": round(self.sim_time, 3),
                        "record_type": "alarm_event",
                        "name": spec.name,
                        "previous_state": previous_alarm,
                        "state": runtime.alarm_state,
                    }
                )

    def run(
        self,
        duration: float | None,
        sink: SinkProtocol,
        realtime: bool = False,
        realtime_scale: float = 1.0,
        command_reader: Callable[[], Optional[Dict[str, float]]] = None,
    ) -> None:
        import itertools
        step_iter = itertools.count() if duration is None else range(int(duration / self.base_step))
        for _ in step_iter:
            commands = command_reader() or {}
            self._update_mv(self.base_step, commands)
            self._update_hidden_states(self.base_step)
            self._emit_mv_updates(sink)
            self._emit_pv_updates(sink)
            self.sim_time += self.base_step
            if realtime:
                time.sleep(self.base_step / max(realtime_scale, 1e-6))

    def summary(self) -> dict:
        category_counts: Dict[str, int] = {}
        for spec in self.pv_specs:
            category_counts[spec.category] = category_counts.get(spec.category, 0) + 1
        return {
            "pv_count": len(self.pv_specs),
            "mv_count": len(self.mv_specs),
            "control_loop_count": 0,
            "categories": category_counts,
        }
