from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List


ALARM_ORDER = ["LOLO", "LO", "NORMAL", "HI", "HIHI"]
ALARM_SEVERITY = {"LOLO": -2, "LO": -1, "NORMAL": 0, "HI": 1, "HIHI": 2}


@dataclass
class RateConfig:
    pressure_period: float = 1.0
    flow_period_min: float = 1.0
    flow_period_max: float = 2.0
    level_period_min: float = 2.0
    level_period_max: float = 5.0
    temperature_period_min: float = 5.0
    temperature_period_max: float = 10.0
    mv_period: float = 1.0
    composition_period_min: float = 360.0
    composition_period_max: float = 900.0
    composition_dead_time: float = 120.0


@dataclass
class AlarmThresholds:
    lolo: float
    lo: float
    hi: float
    hihi: float


@dataclass
class VariableSpec:
    name: str
    category: str
    unit: str
    nominal: float
    state_key: str
    scale: float
    bias: float
    noise: float
    sample_period: float
    min_value: float
    max_value: float
    thresholds: AlarmThresholds
    dead_time: float = 0.0


@dataclass
class MVSpec:
    name: str
    description: str
    command: float
    feedback: float
    min_value: float = 0.0
    max_value: float = 100.0
    rate_limit: float = 8.0
    sample_period: float = 1.0
    thresholds: AlarmThresholds | None = None
    alarm_source: str = "feedback"


FLOW_DEFS = [
    ("pv_001_a_feed_flow", "flow", "kg/s", 62.0, "feed_total", 24.0, 0.0, 0.6, 0.0, 140.0),
    ("pv_002_d_feed_flow", "flow", "kg/s", 54.0, "feed_total", 20.0, 2.0, 0.6, 0.0, 140.0),
    ("pv_003_e_feed_flow", "flow", "kg/s", 48.0, "feed_total", 18.0, 3.0, 0.6, 0.0, 140.0),
    ("pv_004_recycle_flow", "flow", "kg/s", 72.0, "recycle_flow", 28.0, 0.0, 0.7, 0.0, 180.0),
    ("pv_005_purge_flow", "flow", "kg/s", 18.0, "purge_flow", 12.0, 0.0, 0.3, 0.0, 80.0),
    ("pv_006_separator_liquid_flow", "flow", "kg/s", 46.0, "sep_liquid_flow", 22.0, 0.0, 0.5, 0.0, 140.0),
    ("pv_007_stripper_steam_flow", "flow", "kg/s", 24.0, "steam_flow", 14.0, 0.0, 0.3, 0.0, 90.0),
    ("pv_008_reactor_cooling_flow", "flow", "kg/s", 30.0, "cooling_flow", 18.0, 0.0, 0.4, 0.0, 120.0),
    ("pv_009_condenser_cooling_flow", "flow", "kg/s", 26.0, "condenser_flow", 16.0, 0.0, 0.4, 0.0, 110.0),
    ("pv_010_product_flow", "flow", "kg/s", 40.0, "product_flow", 20.0, 0.0, 0.4, 0.0, 130.0),
    ("pv_011_compressor_suction_flow", "flow", "kg/s", 58.0, "compressor_flow", 22.0, 0.0, 0.5, 0.0, 160.0),
    ("pv_012_compressor_discharge_flow", "flow", "kg/s", 60.0, "compressor_flow", 23.0, 2.0, 0.5, 0.0, 170.0),
    ("pv_013_reactor_vent_flow", "flow", "kg/s", 12.0, "vent_flow", 8.0, 0.0, 0.3, 0.0, 60.0),
    ("pv_014_agitator_flush_flow", "flow", "kg/s", 5.0, "aux_flow", 2.0, 4.0, 0.2, 0.0, 40.0),
    ("pv_015_analyzer_sample_flow_1", "flow", "kg/s", 1.4, "aux_flow", 0.6, 0.8, 0.05, 0.0, 10.0),
    ("pv_016_analyzer_sample_flow_2", "flow", "kg/s", 1.2, "aux_flow", 0.5, 0.7, 0.05, 0.0, 10.0),
    ("pv_017_feed_total_flow", "flow", "kg/s", 164.0, "feed_total", 62.0, 0.0, 1.0, 0.0, 300.0),
    ("pv_018_product_recycle_flow", "flow", "kg/s", 26.0, "product_recycle_flow", 10.0, 0.0, 0.3, 0.0, 90.0),
]

PRESSURE_DEFS = [
    ("pv_019_reactor_pressure", "pressure", "kPa", 2700.0, "reactor_pressure", 1.0, 0.0, 8.0, 1500.0, 4200.0),
    ("pv_020_separator_pressure", "pressure", "kPa", 2400.0, "separator_pressure", 1.0, 0.0, 8.0, 1200.0, 3800.0),
    ("pv_021_stripper_pressure", "pressure", "kPa", 2100.0, "stripper_pressure", 1.0, 0.0, 7.0, 1000.0, 3400.0),
    ("pv_022_compressor_discharge_pressure", "pressure", "kPa", 2900.0, "compressor_pressure", 1.0, 0.0, 9.0, 1500.0, 4500.0),
    ("pv_023_purge_header_pressure", "pressure", "kPa", 900.0, "purge_header_pressure", 1.0, 0.0, 4.0, 300.0, 1800.0),
    ("pv_024_condenser_pressure", "pressure", "kPa", 850.0, "condenser_pressure", 1.0, 0.0, 4.0, 300.0, 1800.0),
    ("pv_025_feed_header_pressure", "pressure", "kPa", 1250.0, "feed_header_pressure", 1.0, 0.0, 5.0, 600.0, 2200.0),
    ("pv_026_steam_header_pressure", "pressure", "kPa", 650.0, "steam_header_pressure", 1.0, 0.0, 3.5, 250.0, 1300.0),
    ("pv_027_analyzer_header_pressure", "pressure", "kPa", 300.0, "analyzer_pressure", 1.0, 0.0, 2.0, 100.0, 700.0),
    ("pv_028_reactor_dp", "pressure", "kPa", 220.0, "reactor_dp", 1.0, 0.0, 1.8, 50.0, 450.0),
    ("pv_029_separator_dp", "pressure", "kPa", 180.0, "separator_dp", 1.0, 0.0, 1.5, 40.0, 400.0),
    ("pv_030_column_top_pressure", "pressure", "kPa", 780.0, "column_top_pressure", 1.0, 0.0, 3.0, 250.0, 1500.0),
]

TEMPERATURE_DEFS = [
    ("pv_031_reactor_temperature", "temperature", "degC", 122.0, "reactor_temperature", 1.0, 0.0, 0.35, 40.0, 220.0),
    ("pv_032_separator_temperature", "temperature", "degC", 88.0, "separator_temperature", 1.0, 0.0, 0.25, 20.0, 180.0),
    ("pv_033_stripper_temperature", "temperature", "degC", 110.0, "stripper_temperature", 1.0, 0.0, 0.3, 30.0, 220.0),
    ("pv_034_condenser_temperature", "temperature", "degC", 42.0, "condenser_temperature", 1.0, 0.0, 0.2, 5.0, 120.0),
    ("pv_035_feed_temp_a", "temperature", "degC", 28.0, "feed_temperature", 1.0, -2.0, 0.2, 0.0, 80.0),
    ("pv_036_feed_temp_d", "temperature", "degC", 31.0, "feed_temperature", 1.0, 1.0, 0.2, 0.0, 90.0),
    ("pv_037_feed_temp_e", "temperature", "degC", 34.0, "feed_temperature", 1.0, 3.0, 0.2, 0.0, 95.0),
    ("pv_038_compressor_outlet_temp", "temperature", "degC", 76.0, "compressor_temperature", 1.0, 0.0, 0.25, 10.0, 180.0),
    ("pv_039_cooling_water_inlet_temp", "temperature", "degC", 21.0, "cooling_water_temperature", 1.0, -3.0, 0.15, 0.0, 60.0),
    ("pv_040_cooling_water_outlet_temp", "temperature", "degC", 28.0, "cooling_water_temperature", 1.0, 4.0, 0.15, 0.0, 80.0),
    ("pv_041_steam_temperature", "temperature", "degC", 155.0, "steam_temperature", 1.0, 0.0, 0.35, 60.0, 260.0),
    ("pv_042_product_temperature", "temperature", "degC", 67.0, "product_temperature", 1.0, 0.0, 0.2, 10.0, 140.0),
    ("pv_043_purge_temperature", "temperature", "degC", 46.0, "purge_temperature", 1.0, 0.0, 0.2, 0.0, 120.0),
    ("pv_044_recycle_temperature", "temperature", "degC", 58.0, "recycle_temperature", 1.0, 0.0, 0.2, 0.0, 140.0),
    ("pv_045_analyzer_house_temp", "temperature", "degC", 24.0, "ambient_temperature", 1.0, 0.0, 0.15, 0.0, 50.0),
    ("pv_046_reactor_bed_temp_1", "temperature", "degC", 118.0, "reactor_temperature", 0.98, 0.0, 0.3, 40.0, 220.0),
    ("pv_047_reactor_bed_temp_2", "temperature", "degC", 126.0, "reactor_temperature", 1.02, 0.0, 0.3, 40.0, 230.0),
    ("pv_048_column_bottom_temp", "temperature", "degC", 133.0, "column_bottom_temperature", 1.0, 0.0, 0.25, 30.0, 240.0),
    ("pv_049_column_top_temp", "temperature", "degC", 71.0, "column_top_temperature", 1.0, 0.0, 0.2, 5.0, 150.0),
    ("pv_050_ambient_temp", "temperature", "degC", 23.0, "ambient_temperature", 1.0, 0.0, 0.12, -10.0, 50.0),
]

LEVEL_DEFS = [
    ("pv_051_separator_level", "level", "%", 52.0, "separator_level", 1.0, 0.0, 0.25, 0.0, 100.0),
    ("pv_052_stripper_level", "level", "%", 47.0, "stripper_level", 1.0, 0.0, 0.25, 0.0, 100.0),
    ("pv_053_reflux_drum_level", "level", "%", 55.0, "reflux_drum_level", 1.0, 0.0, 0.2, 0.0, 100.0),
    ("pv_054_condensate_level", "level", "%", 43.0, "condensate_level", 1.0, 0.0, 0.2, 0.0, 100.0),
    ("pv_055_feed_tank_level_a", "level", "%", 68.0, "feed_tank_level", 1.0, 4.0, 0.2, 0.0, 100.0),
    ("pv_056_feed_tank_level_d", "level", "%", 61.0, "feed_tank_level", 1.0, -2.0, 0.2, 0.0, 100.0),
    ("pv_057_feed_tank_level_e", "level", "%", 64.0, "feed_tank_level", 1.0, 2.0, 0.2, 0.0, 100.0),
    ("pv_058_product_tank_level", "level", "%", 58.0, "product_tank_level", 1.0, 0.0, 0.2, 0.0, 100.0),
    ("pv_059_cooling_water_level", "level", "%", 72.0, "utility_level", 1.0, 0.0, 0.2, 0.0, 100.0),
    ("pv_060_wastewater_level", "level", "%", 35.0, "wastewater_level", 1.0, 0.0, 0.2, 0.0, 100.0),
]

COMPOSITION_DEFS = [
    ("pv_061_reactor_a_fraction", "composition", "%", 34.0, "comp_reactor_a", 1.0, 0.0, 0.08, 0.0, 100.0),
    ("pv_062_reactor_b_fraction", "composition", "%", 29.0, "comp_reactor_b", 1.0, 0.0, 0.08, 0.0, 100.0),
    ("pv_063_reactor_c_fraction", "composition", "%", 18.0, "comp_reactor_c", 1.0, 0.0, 0.08, 0.0, 100.0),
    ("pv_064_separator_a_fraction", "composition", "%", 26.0, "comp_separator_a", 1.0, 0.0, 0.08, 0.0, 100.0),
    ("pv_065_separator_b_fraction", "composition", "%", 31.0, "comp_separator_b", 1.0, 0.0, 0.08, 0.0, 100.0),
    ("pv_066_separator_c_fraction", "composition", "%", 22.0, "comp_separator_c", 1.0, 0.0, 0.08, 0.0, 100.0),
    ("pv_067_purge_inerts_fraction", "composition", "%", 12.0, "comp_inerts", 1.0, 0.0, 0.05, 0.0, 100.0),
    ("pv_068_product_g_fraction", "composition", "%", 74.0, "comp_product_g", 1.0, 0.0, 0.06, 0.0, 100.0),
    ("pv_069_product_h_fraction", "composition", "%", 18.0, "comp_product_h", 1.0, 0.0, 0.06, 0.0, 100.0),
    ("pv_070_recycle_a_fraction", "composition", "%", 24.0, "comp_recycle_a", 1.0, 0.0, 0.06, 0.0, 100.0),
    ("pv_071_recycle_b_fraction", "composition", "%", 36.0, "comp_recycle_b", 1.0, 0.0, 0.06, 0.0, 100.0),
    ("pv_072_offgas_methane_fraction", "composition", "%", 9.0, "comp_offgas_light", 1.0, 0.0, 0.04, 0.0, 100.0),
    ("pv_073_column_bottom_heavy_fraction", "composition", "%", 81.0, "comp_column_heavy", 1.0, 0.0, 0.06, 0.0, 100.0),
]

MV_DEFS = [
    ("mv_01_a_feed_valve", "A feed valve", 52.0),
    ("mv_02_d_feed_valve", "D feed valve", 48.0),
    ("mv_03_e_feed_valve", "E feed valve", 44.0),
    ("mv_04_recycle_valve", "Recycle valve", 55.0),
    ("mv_05_purge_valve", "Purge valve", 22.0),
    ("mv_06_reactor_coolant_valve", "Reactor coolant valve", 46.0),
    ("mv_07_separator_dump_valve", "Separator dump valve", 43.0),
    ("mv_08_stripper_steam_valve", "Stripper steam valve", 50.0),
    ("mv_09_product_draw_valve", "Product draw valve", 45.0),
    ("mv_10_compressor_speed", "Compressor speed command", 58.0),
    ("mv_11_condenser_cooling_valve", "Condenser cooling valve", 42.0),
    ("mv_12_analyzer_selector", "Analyzer selector bias", 50.0),
]

DEFAULT_HIDDEN_STATES: Dict[str, float] = {
    "feed_total": 2.6,
    "recycle_flow": 2.55,
    "purge_flow": 1.5,
    "sep_liquid_flow": 2.1,
    "steam_flow": 1.75,
    "cooling_flow": 1.7,
    "condenser_flow": 1.6,
    "product_flow": 2.0,
    "compressor_flow": 2.55,
    "vent_flow": 1.3,
    "aux_flow": 0.7,
    "product_recycle_flow": 1.0,
    "reactor_pressure": 2700.0,
    "separator_pressure": 2400.0,
    "stripper_pressure": 2100.0,
    "compressor_pressure": 2900.0,
    "purge_header_pressure": 900.0,
    "condenser_pressure": 850.0,
    "feed_header_pressure": 1250.0,
    "steam_header_pressure": 650.0,
    "analyzer_pressure": 300.0,
    "reactor_dp": 220.0,
    "separator_dp": 180.0,
    "column_top_pressure": 780.0,
    "reactor_temperature": 122.0,
    "separator_temperature": 88.0,
    "stripper_temperature": 110.0,
    "condenser_temperature": 42.0,
    "feed_temperature": 30.0,
    "compressor_temperature": 76.0,
    "cooling_water_temperature": 24.0,
    "steam_temperature": 155.0,
    "product_temperature": 67.0,
    "purge_temperature": 46.0,
    "recycle_temperature": 58.0,
    "ambient_temperature": 23.0,
    "column_bottom_temperature": 133.0,
    "column_top_temperature": 71.0,
    "separator_level": 52.0,
    "stripper_level": 47.0,
    "reflux_drum_level": 55.0,
    "condensate_level": 43.0,
    "feed_tank_level": 64.0,
    "product_tank_level": 58.0,
    "utility_level": 72.0,
    "wastewater_level": 35.0,
    "comp_reactor_a": 34.0,
    "comp_reactor_b": 29.0,
    "comp_reactor_c": 18.0,
    "comp_separator_a": 26.0,
    "comp_separator_b": 31.0,
    "comp_separator_c": 22.0,
    "comp_inerts": 12.0,
    "comp_product_g": 74.0,
    "comp_product_h": 18.0,
    "comp_recycle_a": 24.0,
    "comp_recycle_b": 36.0,
    "comp_offgas_light": 9.0,
    "comp_column_heavy": 81.0,
}


def build_default_pv_specs(rates: RateConfig, rng: random.Random) -> List[VariableSpec]:
    def banded_thresholds(nominal: float, low_pct: float, hi_pct: float, min_value: float, max_value: float) -> AlarmThresholds:
        return AlarmThresholds(
            lolo=max(min_value, nominal * (1.0 - low_pct * 1.7)),
            lo=max(min_value, nominal * (1.0 - low_pct)),
            hi=min(max_value, nominal * (1.0 + hi_pct)),
            hihi=min(max_value, nominal * (1.0 + hi_pct * 1.7)),
        )

    def absolute_thresholds(nominal: float, low_delta: float, hi_delta: float, min_value: float, max_value: float) -> AlarmThresholds:
        return AlarmThresholds(
            lolo=max(min_value, nominal - low_delta * 1.7),
            lo=max(min_value, nominal - low_delta),
            hi=min(max_value, nominal + hi_delta),
            hihi=min(max_value, nominal + hi_delta * 1.7),
        )

    def pick_period(category: str) -> float:
        if category == "pressure":
            return rates.pressure_period
        if category == "flow":
            return rng.uniform(rates.flow_period_min, rates.flow_period_max)
        if category == "level":
            return rng.uniform(rates.level_period_min, rates.level_period_max)
        if category == "temperature":
            return rng.uniform(rates.temperature_period_min, rates.temperature_period_max)
        if category == "composition":
            return rng.uniform(rates.composition_period_min, rates.composition_period_max)
        return rates.mv_period

    specs: List[VariableSpec] = []
    definitions = FLOW_DEFS + PRESSURE_DEFS + TEMPERATURE_DEFS + LEVEL_DEFS + COMPOSITION_DEFS
    for name, category, unit, nominal, state_key, scale, bias, noise, min_value, max_value in definitions:
        if category in {"flow", "pressure", "level"}:
            thresholds = banded_thresholds(nominal, 0.15, 0.15, min_value, max_value)
        elif category == "temperature":
            thresholds = absolute_thresholds(nominal, 6.0, 6.0, min_value, max_value)
        else:
            thresholds = absolute_thresholds(nominal, 8.0, 8.0, min_value, max_value)
        specs.append(
            VariableSpec(
                name=name,
                category=category,
                unit=unit,
                nominal=nominal,
                state_key=state_key,
                scale=scale,
                bias=bias,
                noise=noise,
                sample_period=pick_period(category),
                min_value=min_value,
                max_value=max_value,
                thresholds=thresholds,
                dead_time=rates.composition_dead_time if category == "composition" else 0.0,
            )
        )
    return specs


def build_default_mv_specs(rates: RateConfig) -> List[MVSpec]:
    def mv_thresholds(nominal: float, min_value: float, max_value: float) -> AlarmThresholds:
        span = max_value - min_value
        lo = max(min_value, nominal - 0.20 * span)
        lolo = max(min_value, nominal - 0.35 * span)
        hi = min(max_value, nominal + 0.20 * span)
        hihi = min(max_value, nominal + 0.35 * span)
        return AlarmThresholds(lolo=lolo, lo=lo, hi=hi, hihi=hihi)

    return [
        MVSpec(
            name=name,
            description=description,
            command=start,
            feedback=start,
            sample_period=rates.mv_period,
            thresholds=mv_thresholds(start, 0.0, 100.0),
            alarm_source="feedback",
        )
        for name, description, start in MV_DEFS
    ]


def build_default_hidden_states() -> Dict[str, float]:
    return dict(DEFAULT_HIDDEN_STATES)



