from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ControlLoopSpec:
    name: str
    pv_name: str
    mv_name: str
    setpoint: float
    gain: float
    integral_time: float
    output_bias: float
    reverse_action: bool = False
    enabled: bool = True


@dataclass
class OverrideRule:
    name: str
    pv_name: str
    trigger_state: str
    mv_name: str
    mode: str
    value: float


CONTROL_LOOPS = [
    ControlLoopSpec("fc_a_feed", "pv_001_a_feed_flow", "mv_01_a_feed_valve", 62.0, 0.8, 30.0, 52.0),
    ControlLoopSpec("fc_d_feed", "pv_002_d_feed_flow", "mv_02_d_feed_valve", 54.0, 0.8, 30.0, 48.0),
    ControlLoopSpec("fc_e_feed", "pv_003_e_feed_flow", "mv_03_e_feed_valve", 48.0, 0.8, 30.0, 44.0),
    ControlLoopSpec("fc_recycle", "pv_004_recycle_flow", "mv_04_recycle_valve", 72.0, 0.6, 35.0, 55.0),
    ControlLoopSpec("fc_product", "pv_010_product_flow", "mv_09_product_draw_valve", 40.0, 0.7, 28.0, 45.0),
    ControlLoopSpec("pc_reactor", "pv_019_reactor_pressure", "mv_05_purge_valve", 2700.0, 0.03, 45.0, 22.0),
    ControlLoopSpec("pc_separator", "pv_020_separator_pressure", "mv_11_condenser_cooling_valve", 2400.0, 0.02, 55.0, 42.0, reverse_action=True),
    ControlLoopSpec("pc_compressor", "pv_022_compressor_discharge_pressure", "mv_10_compressor_speed", 2900.0, 0.03, 50.0, 58.0),
    ControlLoopSpec("lc_separator", "pv_051_separator_level", "mv_07_separator_dump_valve", 52.0, 0.9, 40.0, 43.0),
    ControlLoopSpec("lc_stripper", "pv_052_stripper_level", "mv_09_product_draw_valve", 47.0, 0.8, 42.0, 45.0),
    ControlLoopSpec("lc_product_tank", "pv_058_product_tank_level", "mv_09_product_draw_valve", 58.0, 0.5, 60.0, 45.0, reverse_action=True),
    ControlLoopSpec("tc_reactor", "pv_031_reactor_temperature", "mv_06_reactor_coolant_valve", 122.0, 1.2, 80.0, 46.0, reverse_action=True),
    ControlLoopSpec("tc_condenser", "pv_034_condenser_temperature", "mv_11_condenser_cooling_valve", 42.0, 0.9, 70.0, 42.0, reverse_action=True),
    ControlLoopSpec("tc_stripper", "pv_033_stripper_temperature", "mv_08_stripper_steam_valve", 110.0, 0.9, 65.0, 50.0),
    ControlLoopSpec("qc_product_g", "pv_068_product_g_fraction", "mv_08_stripper_steam_valve", 74.0, 0.3, 600.0, 50.0),
    ControlLoopSpec("qc_recycle_a", "pv_070_recycle_a_fraction", "mv_12_analyzer_selector", 24.0, 0.5, 600.0, 50.0),
    ControlLoopSpec("ratio_feed_total", "pv_017_feed_total_flow", "mv_04_recycle_valve", 164.0, 0.2, 50.0, 55.0),
]


OVERRIDE_RULES = [
    OverrideRule("reactor_pressure_hi", "pv_019_reactor_pressure", "HI", "mv_05_purge_valve", "min", 70.0),
    OverrideRule("reactor_pressure_hihi", "pv_019_reactor_pressure", "HIHI", "mv_05_purge_valve", "min", 92.0),
    OverrideRule("reactor_temp_hi", "pv_031_reactor_temperature", "HI", "mv_06_reactor_coolant_valve", "min", 75.0),
    OverrideRule("reactor_temp_hihi", "pv_031_reactor_temperature", "HIHI", "mv_06_reactor_coolant_valve", "min", 95.0),
    OverrideRule("separator_level_hi", "pv_051_separator_level", "HI", "mv_07_separator_dump_valve", "min", 70.0),
    OverrideRule("separator_level_lolo", "pv_051_separator_level", "LOLO", "mv_07_separator_dump_valve", "max", 8.0),
    OverrideRule("stripper_level_hihi", "pv_052_stripper_level", "HIHI", "mv_09_product_draw_valve", "min", 88.0),
    OverrideRule("compressor_pressure_hihi", "pv_022_compressor_discharge_pressure", "HIHI", "mv_10_compressor_speed", "max", 35.0),
]


def build_default_control_loops() -> List[ControlLoopSpec]:
    return list(CONTROL_LOOPS)


def build_default_override_rules() -> List[OverrideRule]:
    return list(OVERRIDE_RULES)
