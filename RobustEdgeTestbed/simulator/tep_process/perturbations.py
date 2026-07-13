from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from simulator.tep_process.sinks import BaseSink


@dataclass
class PerturbationConfig:
    """
    Configuration for all supported perturbation effects in the simulator.
    Use from_profile() to construct a config for a given profile (light, moderate, heavy).
    """
    drop_prob: float
    pv_drop_prob: float
    mv_drop_prob: float
    alarm_drop_prob: float
    duplicate_prob: float
    burst_duplicate_prob: float
    clock_skew: float
    timestamp_jitter: float
    latency_jitter_min: float
    latency_jitter_max: float
    out_of_order_prob: float
    outage_start_prob: float
    outage_duration_min: float
    outage_duration_max: float
    tag_swap_prob: float
    unit_scale_error_prob: float
    stuck_prob: float
    quantization_step: float
    downsample_every: int
    silent_sensors: Tuple[str, ...] = ()
    downsample_pvs: Optional[Tuple[str, ...]] = None

    @staticmethod
    def from_profile(profile: str = None, silent_sensors: Optional[List[str]] = None, downsample_pvs: Optional[List[str]] = None, enabled: bool = None) -> "PerturbationConfig":
        """
        Factory for perturbation configs. Sets defaults for standalone runs.
        If enabled is False, returns a config with all effects disabled.
        """
        if enabled is None:
            enabled = True
        if profile is None:
            profile = "moderate"
        if not enabled:
            # If perturbations are disabled, return a config with no faults
            return PerturbationConfig(
                drop_prob=0.0,
                pv_drop_prob=0.0,
                mv_drop_prob=0.0,
                alarm_drop_prob=0.0,
                duplicate_prob=0.0,
                burst_duplicate_prob=0.0,
                clock_skew=0.0,
                timestamp_jitter=0.0,
                latency_jitter_min=0.0,
                latency_jitter_max=0.0,
                out_of_order_prob=0.0,
                outage_start_prob=0.0,
                outage_duration_min=0.0,
                outage_duration_max=0.0,
                tag_swap_prob=0.0,
                unit_scale_error_prob=0.0,
                stuck_prob=0.0,
                quantization_step=0.0,
                downsample_every=1,
                silent_sensors=tuple(silent_sensors or []),
                downsample_pvs=tuple(downsample_pvs or []) if downsample_pvs else None,
            )
        profile = profile.lower()
        if profile == "light":
            return PerturbationConfig(
                drop_prob=0.01,
                pv_drop_prob=0.02,
                mv_drop_prob=0.01,
                alarm_drop_prob=0.01,
                duplicate_prob=0.005,
                burst_duplicate_prob=0.08,
                clock_skew=0.05,
                timestamp_jitter=0.05,
                latency_jitter_min=0.0,
                latency_jitter_max=0.3,
                out_of_order_prob=0.02,
                outage_start_prob=0.0005,
                outage_duration_min=2.0,
                outage_duration_max=8.0,
                tag_swap_prob=0.002,
                unit_scale_error_prob=0.002,
                stuck_prob=0.002,
                quantization_step=0.02,
                downsample_every=1,
                silent_sensors=tuple(silent_sensors or []),
                downsample_pvs=tuple(downsample_pvs or []) if downsample_pvs else None,
            )
        if profile == "heavy":
            return PerturbationConfig(
                drop_prob=0.05,
                pv_drop_prob=0.09,
                mv_drop_prob=0.06,
                alarm_drop_prob=0.08,
                duplicate_prob=0.04,
                burst_duplicate_prob=0.35,
                clock_skew=0.8,
                timestamp_jitter=0.6,
                latency_jitter_min=0.1,
                latency_jitter_max=3.5,
                out_of_order_prob=0.25,
                outage_start_prob=0.003,
                outage_duration_min=8.0,
                outage_duration_max=35.0,
                tag_swap_prob=0.03,
                unit_scale_error_prob=0.02,
                stuck_prob=0.03,
                quantization_step=0.25,
                downsample_every=3,
                silent_sensors=tuple(silent_sensors or []),
            )
        return PerturbationConfig(
            drop_prob=0.025,
            pv_drop_prob=0.05,
            mv_drop_prob=0.03,
            alarm_drop_prob=0.04,
            duplicate_prob=0.015,
            burst_duplicate_prob=0.20,
            clock_skew=0.2,
            timestamp_jitter=0.2,
            latency_jitter_min=0.0,
            latency_jitter_max=1.2,
            out_of_order_prob=0.08,
            outage_start_prob=0.0015,
            outage_duration_min=4.0,
            outage_duration_max=20.0,
            tag_swap_prob=0.01,
            unit_scale_error_prob=0.008,
            stuck_prob=0.01,
            quantization_step=0.1,
            downsample_every=2,
            silent_sensors=tuple(silent_sensors or []),
            downsample_pvs=tuple(downsample_pvs or []) if downsample_pvs else None,
        )


class PerturbationSink(BaseSink):
    def __init__(self, downstream: BaseSink, cfg: PerturbationConfig, seed: int = 10871):
        self.downstream = downstream
        self.cfg = cfg
        self.random = random.Random(seed)

        self.channel_counts: Dict[str, int] = {}
        self.stuck_values: Dict[Tuple[str, str], float] = {}
        self.name_cache: Dict[str, List[str]] = {}

        self.outage_until: Optional[float] = None
        self.buffered: List[Tuple[dict, bool]] = []

        self.pending: List[Tuple[float, dict, bool]] = []

    def emit_measurement(self, record: dict) -> None:
        self._ingest(record, is_event=False)

    def emit_event(self, record: dict) -> None:
        self._ingest(record, is_event=True)

    def close(self) -> None:
        self._flush_buffered(force_ts=float("inf"))
        self._deliver_ready(float("inf"))
        self.downstream.close()

    def _ingest(self, record: dict, is_event: bool) -> None:
        rec = dict(record)
        now_ts = float(rec.get("timestamp", 0.0))

        if self._is_silent(rec):
            self._deliver_ready(now_ts)
            return

        if self._is_outage_active(now_ts):
            self.buffered.append((rec, is_event))
            return

        if self._should_start_outage(now_ts):
            self.buffered.append((rec, is_event))
            return

        self._flush_buffered(now_ts)

        rec = self._apply_timing(rec)
        rec = self._apply_tag_issues(rec)
        rec = self._apply_sensor_faults(rec)

        if self._should_drop(rec):
            self._deliver_ready(now_ts)
            return

        release_ts = self._release_time(rec)
        self.pending.append((release_ts, rec, is_event))

        for dup in self._maybe_duplicate(rec):
            self.pending.append((self._release_time(dup), dup, is_event))

        self._deliver_ready(now_ts)

    def _is_silent(self, rec: dict) -> bool:
        return rec.get("name") in self.cfg.silent_sensors

    def _is_outage_active(self, ts: float) -> bool:
        if self.outage_until is None:
            return False
        if ts < self.outage_until:
            return True
        self.outage_until = None
        return False

    def _should_start_outage(self, ts: float) -> bool:
        if self.outage_until is not None:
            return False
        if self.random.random() < self.cfg.outage_start_prob:
            self.outage_until = ts + self.random.uniform(self.cfg.outage_duration_min, self.cfg.outage_duration_max)
            return True
        return False

    def _flush_buffered(self, force_ts: float) -> None:
        if self.buffered and self.outage_until is None:
            burst = list(self.buffered)
            self.buffered.clear()
            for rec, is_event in burst:
                rec2 = self._apply_timing(dict(rec))
                rec2["timestamp"] = float(force_ts)
                rec2 = self._apply_tag_issues(rec2)
                rec2 = self._apply_sensor_faults(rec2)
                if self._should_drop(rec2):
                    continue
                self.pending.append((float(force_ts), rec2, is_event))
                if self.random.random() < self.cfg.burst_duplicate_prob:
                    dup = dict(rec2)
                    dup["timestamp"] = float(force_ts)
                    self.pending.append((float(force_ts), dup, is_event))

    def _apply_timing(self, rec: dict) -> dict:
        base_ts = float(rec.get("timestamp", 0.0))
        jitter = self.random.uniform(-self.cfg.timestamp_jitter, self.cfg.timestamp_jitter)
        rec["timestamp"] = max(0.0, base_ts + self.cfg.clock_skew + jitter)
        return rec

    def _apply_tag_issues(self, rec: dict) -> dict:
        rtype = str(rec.get("record_type", "unknown"))
        name = str(rec.get("name", "unknown"))
        cache = self.name_cache.setdefault(rtype, [])
        if name not in cache:
            cache.append(name)

        if cache and len(cache) > 1 and self.random.random() < self.cfg.tag_swap_prob:
            choices = [n for n in cache if n != name]
            if choices:
                rec["name"] = self.random.choice(choices)
                if "source" in rec:
                    rec["source"] = rec["name"]

        if rec.get("record_type") == "pv" and self.random.random() < self.cfg.unit_scale_error_prob:
            if "value" in rec:
                factor = 10.0 if self.random.random() < 0.5 else 0.1
                rec["value"] = float(rec["value"]) * factor
                rec["unit"] = str(rec.get("unit", "")) + "_scaled"
        return rec

    def _apply_sensor_faults(self, rec: dict) -> dict:
        name = str(rec.get("name", "unknown"))
        channel_key = (str(rec.get("record_type", "unknown")), name)

        # Only downsample if PV is in downsample_pvs (if set), or if downsample_pvs is None (all PVs)
        apply_downsample = (
            self.cfg.downsample_pvs is None or name in self.cfg.downsample_pvs
        )
        cnt = self.channel_counts.get(name, 0) + 1
        self.channel_counts[name] = cnt
        if (
            rec.get("record_type") == "pv"
            and self.cfg.downsample_every > 1
            and apply_downsample
            and cnt % self.cfg.downsample_every != 0
        ):
            rec["_drop_downsample"] = True
            return rec

        for field in ("value", "feedback", "command"):
            if field not in rec:
                continue
            val = float(rec[field])
            key = (field, name)
            if key not in self.stuck_values and self.random.random() < self.cfg.stuck_prob:
                self.stuck_values[key] = val
            if key in self.stuck_values:
                val = self.stuck_values[key]
            if self.cfg.quantization_step > 0:
                step = self.cfg.quantization_step
                val = round(val / step) * step
            rec[field] = val
        return rec

    def _should_drop(self, rec: dict) -> bool:
        if rec.pop("_drop_downsample", False):
            return True
        if self.random.random() < self.cfg.drop_prob:
            return True

        rtype = rec.get("record_type")
        if rtype == "pv" and self.random.random() < self.cfg.pv_drop_prob:
            return True
        if rtype == "mv" and self.random.random() < self.cfg.mv_drop_prob:
            return True
        if rtype in {"alarm_state", "alarm_event"} and self.random.random() < self.cfg.alarm_drop_prob:
            return True
        return False

    def _release_time(self, rec: dict) -> float:
        ts = float(rec.get("timestamp", 0.0))
        latency = self.random.uniform(self.cfg.latency_jitter_min, self.cfg.latency_jitter_max)
        return ts + latency

    def _maybe_duplicate(self, rec: dict) -> List[dict]:
        out: List[dict] = []
        if self.random.random() < self.cfg.duplicate_prob:
            out.append(dict(rec))
        return out

    def _deliver_ready(self, now_ts: float) -> None:
        ready = [item for item in self.pending if item[0] <= now_ts]
        self.pending = [item for item in self.pending if item[0] > now_ts]
        if not ready:
            return
        if self.random.random() < self.cfg.out_of_order_prob:
            self.random.shuffle(ready)
        else:
            ready.sort(key=lambda x: x[0])

        for _, rec, is_event in ready:
            if is_event:
                self.downstream.emit_event(rec)
            else:
                self.downstream.emit_measurement(rec)


# ---------------------------------------------------------------------------
# ETFA 2026 lambda-based perturbation system
# ---------------------------------------------------------------------------


def _stable_tag_hash(tag: str) -> int:
    """Stable, cross-process hash for a tag string.

    Python's built-in hash() is randomised by PYTHONHASHSEED and must not be
    used for any computation that needs to agree across processes (e.g. between
    the host writing scenario.json and the Docker container running the sink).
    We take the first 8 bytes of SHA-256 as a uint64, which is constant for a
    given input string regardless of process, platform, or Python version.
    """
    digest = hashlib.sha256(tag.encode("utf-8", errors="replace")).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass
class PerturbationSpec:
    """
    Specifies a single ETFA 2026 perturbation family (P1–P5) with continuous
    severity λ ∈ [0, 1].  λ=0.0 → no perturbation; λ=1.0 → maximum.

    For tag-scoped families (P1, P2, P3, P5) severity controls both:
      - the fraction of eligible tags affected: f_aff = λ * f_max (default f_max=0.50)
      - the per-tag perturbation intensity (p_drop, p_dup, j_amplitude, or q)

    P4 is global by default: outage_start must be set externally (see run_data_campaign.py)
    to align the outage/flush window with the attack window.

    Tag selection algorithm (for metadata and reproducibility):
      for tag t: affected = Random(seed XOR sha256(t)[:8]).random() < f_aff
      Uses a stable cross-process hash (not Python's built-in hash()) so that
      the host process (scenario.json) and the container process (LambdaPerturbationSink)
      always agree on which tags are affected.
    """

    family: str        # "none" | "P1" | "P2" | "P3" | "P4" | "P5"
    lambda_: float     # severity ∈ [0.0, 1.0]
    seed: int
    # P4 only: simulation time (seconds) when the outage begins; None disables buffering
    outage_start: Optional[float] = None

    # Tag-scope maximum (applies to P1, P2, P3, P5)
    f_max: float = 0.50

    # P1 – Record loss
    p_max: float = 0.30

    # P2 – Duplicate records
    d_max: float = 0.20
    dup_shift_min: float = 0.001   # minimum duplicate timestamp shift (seconds)
    dup_shift_max: float = 0.500   # maximum duplicate timestamp shift (seconds)

    # P3 – Timing disorder
    j_max: float = 30.0            # maximum jitter amplitude (seconds)

    # P4 – Buffered delivery
    d_max_p4: float = 600.0        # maximum outage duration (seconds)
    kappa_max: float = 20.0        # maximum burst compression factor

    # P5 – Rate degradation: q derived from lambda_ only

    @property
    def f_aff(self) -> float:
        """Fraction of eligible tags that are affected (0 for P4/none)."""
        if self.family in ("none", "P4"):
            return 0.0
        return self.lambda_ * self.f_max

    @property
    def p_drop(self) -> float:
        """P1: per-record drop probability for affected tags."""
        return self.lambda_ * self.p_max

    @property
    def p_dup(self) -> float:
        """P2: per-record duplication probability for affected tags."""
        return self.lambda_ * self.d_max

    @property
    def j_amplitude(self) -> float:
        """P3: jitter amplitude in seconds; actual jitter drawn from Uniform[-j, +j]."""
        return self.lambda_ * self.j_max

    @property
    def d_out(self) -> float:
        """P4: outage duration in seconds."""
        return self.lambda_ * self.d_max_p4

    @property
    def kappa(self) -> float:
        """P4: burst compression factor."""
        return 1.0 + self.lambda_ * (self.kappa_max - 1.0)

    @property
    def d_flush(self) -> float:
        """P4: approximate flush (burst) duration in seconds."""
        return self.d_out / max(self.kappa, 1e-9)

    @property
    def q(self) -> int:
        """P5: keep every q-th record per affected tag; q=1 means no degradation."""
        return 1 + int(math.floor(9.0 * max(0.0, min(1.0, self.lambda_))))

    def concrete_params(self) -> dict:
        """Return all derived concrete parameters for inclusion in scenario.json."""
        base: dict = {
            "family": self.family,
            "lambda": self.lambda_,
            "seed": self.seed,
            "f_aff": round(self.f_aff, 6),
            "f_max": self.f_max,
            "tag_selection_algo": (
                "per-tag: Random(seed ^ sha256(tag)[:8]).random() < f_aff; at-least-one tag forced when lambda>0"
            ),
        }
        if self.family == "P1":
            base.update({"p_drop": round(self.p_drop, 6), "p_max": self.p_max})
        elif self.family == "P2":
            base.update({
                "p_dup": round(self.p_dup, 6),
                "d_max": self.d_max,
                "dup_shift_min_s": self.dup_shift_min,
                "dup_shift_max_s": self.dup_shift_max,
            })
        elif self.family == "P3":
            base.update({
                "j_amplitude_s": round(self.j_amplitude, 6),
                "j_max_s": self.j_max,
                "jitter_distribution": "Uniform[-j_amplitude, +j_amplitude]",
            })
        elif self.family == "P4":
            base.update({
                "d_out_s": round(self.d_out, 3),
                "d_max_p4_s": self.d_max_p4,
                "kappa": round(self.kappa, 6),
                "kappa_max": self.kappa_max,
                "d_flush_s": round(self.d_flush, 3),
                "outage_start_sim_s": self.outage_start,
                "scope": "global: all records during outage interval are buffered",
            })
        elif self.family == "P5":
            base.update({
                "q": self.q,
                "note": "keep every q-th record per affected tag",
            })
        return base

    def compute_affected_tags(self, eligible_tags: List[str]) -> List[str]:
        """Deterministically compute which tags are affected from the eligible list.

        Uses the same algorithm as LambdaPerturbationSink._is_affected so that the
        stored scenario.json list exactly matches what the sink will apply.

        Guarantees at least one affected tag when lambda > 0 and eligible_tags is
        non-empty (per Section 2.1: low-severity runs still produce a measurable
        perturbation).  The fallback tag is chosen by minimum per-tag RNG value
        so the selection remains deterministic.
        """
        if self.lambda_ <= 0.0 or not eligible_tags:
            return []
        affected = [
            t for t in eligible_tags
            if random.Random(self.seed ^ _stable_tag_hash(t)).random() < self.f_aff
        ]
        if not affected:
            # Force the tag whose sub-RNG value is lowest (deterministic tiebreaker)
            affected = [min(
                eligible_tags,
                key=lambda t: random.Random(self.seed ^ _stable_tag_hash(t)).random(),
            )]
        return sorted(affected)


class LambdaPerturbationSink(BaseSink):
    """
    Implements ETFA 2026 perturbation families P1–P5 with continuous severity λ.
    Exactly one family is active per instance.  All perturbations are injected
    during telemetry/alarm/controller delivery, before InfluxDB ingestion.

    P1  Record loss        – Bernoulli drop per record for affected tags
    P2  Duplicate records  – extra copy with shifted timestamp for affected tags
    P3  Timing disorder    – uniform timestamp jitter for affected tags
    P4  Buffered delivery  – global outage window followed by compressed burst flush
    P5  Rate degradation   – keep every q-th record for affected tags
    """

    def __init__(self, downstream: BaseSink, spec: PerturbationSpec) -> None:
        self.downstream = downstream
        self.spec = spec
        self._rng = random.Random(spec.seed)
        # Per-tag affected cache: tag str -> bool (populated lazily or pre-loaded)
        self._tag_affected: Dict[str, bool] = {}
        # Pre-populate from known TEP tag list so the at-least-one guarantee
        # (Section 2.1) is enforced inside the container without any extra args.
        if spec.family not in ("none", "P4") and spec.lambda_ > 0.0:
            try:
                from simulator.tep_process.config import (
                    FLOW_DEFS, PRESSURE_DEFS, TEMPERATURE_DEFS,
                    LEVEL_DEFS, COMPOSITION_DEFS, MV_DEFS,
                )
                _all_defs = (
                    FLOW_DEFS + PRESSURE_DEFS + TEMPERATURE_DEFS
                    + LEVEL_DEFS + COMPOSITION_DEFS
                )
                _pv_tags = [d[0] for d in _all_defs]
                _mv_tags = [d[0] for d in MV_DEFS]
                eligible = _pv_tags + _mv_tags
                for t in spec.compute_affected_tags(eligible):
                    self._tag_affected[t] = True
                # Mark all eligible tags not in the affected set as False so
                # _is_affected never re-evaluates them via the probabilistic path.
                for t in eligible:
                    if t not in self._tag_affected:
                        self._tag_affected[t] = False
            except ImportError:
                pass  # fall back to lazy probabilistic selection
        # P4 state
        self._p4_buffer: List[Tuple[dict, bool]] = []
        self._p4_flushed: bool = False
        # P5 per-tag record counters
        self._p5_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------ BaseSink

    def emit_measurement(self, record: dict) -> None:
        self._process(record, is_event=False)

    def emit_event(self, record: dict) -> None:
        self._process(record, is_event=True)

    def close(self) -> None:
        # P4: flush any remaining buffered records at stream end
        if self.spec.family == "P4" and self._p4_buffer and not self._p4_flushed:
            self._p4_flush()
        self.downstream.close()

    # ------------------------------------------------------------------ routing

    def _process(self, record: dict, is_event: bool) -> None:
        family = self.spec.family
        if family == "none" or self.spec.lambda_ <= 0.0:
            self._emit(record, is_event)
            return
        tag = str(record.get("name") or record.get("source") or "")
        if family == "P1":
            self._apply_p1(record, tag, is_event)
        elif family == "P2":
            self._apply_p2(record, tag, is_event)
        elif family == "P3":
            self._apply_p3(record, tag, is_event)
        elif family == "P4":
            self._apply_p4(record, is_event)
        elif family == "P5":
            self._apply_p5(record, tag, is_event)
        else:
            self._emit(record, is_event)

    def _emit(self, record: dict, is_event: bool) -> None:
        if is_event:
            self.downstream.emit_event(record)
        else:
            self.downstream.emit_measurement(record)

    # ------------------------------------------------------------------ tag scope

    def _is_affected(self, tag: str) -> bool:
        """Deterministic per-tag selection using a stable (non-PYTHONHASHSEED) hash.

        Records with no identifiable tag (empty string) are never perturbed so
        that unknown record types pass through unmodified.
        """
        if not tag:
            return False
        if tag not in self._tag_affected:
            sub_seed = self.spec.seed ^ _stable_tag_hash(tag)
            self._tag_affected[tag] = random.Random(sub_seed).random() < self.spec.f_aff
        return self._tag_affected[tag]

    # ------------------------------------------------------------------ P1: Record loss

    def _apply_p1(self, record: dict, tag: str, is_event: bool) -> None:
        if self._is_affected(tag) and self._rng.random() < self.spec.p_drop:
            return  # drop record before InfluxDB ingestion
        self._emit(record, is_event)

    # ------------------------------------------------------------------ P2: Duplicate records

    def _apply_p2(self, record: dict, tag: str, is_event: bool) -> None:
        self._emit(record, is_event)  # always emit the original
        if self._is_affected(tag) and self._rng.random() < self.spec.p_dup:
            dup = dict(record)
            # Shift timestamp to avoid InfluxDB overwriting identical tag/ts combinations
            shift = self._rng.uniform(self.spec.dup_shift_min, self.spec.dup_shift_max)
            dup["timestamp"] = float(dup.get("timestamp", 0.0)) + shift
            self._emit(dup, is_event)

    # ------------------------------------------------------------------ P3: Timing disorder

    def _apply_p3(self, record: dict, tag: str, is_event: bool) -> None:
        rec = dict(record)
        if self._is_affected(tag):
            j = self.spec.j_amplitude
            epsilon = self._rng.uniform(-j, j)
            orig_ts = float(rec.get("timestamp", 0.0))
            # Preserve original timestamp for reproducibility/metadata (spec §4 P3).
            # Do NOT clamp: clamping near t=0 would truncate the left tail of the
            # jitter distribution and bias the perturbation.
            rec["original_timestamp"] = orig_ts
            rec["timestamp"] = orig_ts + epsilon
        self._emit(rec, is_event)

    # ------------------------------------------------------------------ P4: Buffered delivery

    def _apply_p4(self, record: dict, is_event: bool) -> None:
        if self.spec.outage_start is None or self.spec.lambda_ <= 0.0:
            self._emit(record, is_event)
            return
        ts = float(record.get("timestamp", 0.0))
        outage_end = self.spec.outage_start + self.spec.d_out
        if self._p4_flushed or ts < self.spec.outage_start:
            self._emit(record, is_event)
            return
        if ts < outage_end:
            self._p4_buffer.append((dict(record), is_event))
            return
        # Record is past outage end — flush buffer then pass record through
        if not self._p4_flushed:
            self._p4_flush()
        self._emit(record, is_event)

    def _p4_flush(self) -> None:
        """Release buffered records as a time-compressed burst anchored at outage_end.

        The reconnect moment is outage_end = outage_start + D_out.  All buffered
        records are replayed starting from outage_end with timestamps compressed by
        kappa:

          t'(rec) = outage_end + (orig_ts - outage_start) / kappa

        This places the entire burst in the interval [outage_end, outage_end + D_flush]
        where D_flush = D_out / kappa, matching the spec formula.
        The original generation timestamp is preserved as original_timestamp.
        """
        if not self._p4_buffer:
            self._p4_flushed = True
            return
        outage_start = float(self.spec.outage_start)  # type: ignore[arg-type]
        outage_end = outage_start + self.spec.d_out
        kappa = max(self.spec.kappa, 1.0)
        for rec, is_event in self._p4_buffer:
            rec = dict(rec)
            orig_ts = float(rec.get("timestamp", outage_start))
            rec["original_timestamp"] = orig_ts
            rec["timestamp"] = outage_end + (orig_ts - outage_start) / kappa
            self._emit(rec, is_event)
        self._p4_buffer.clear()
        self._p4_flushed = True

    # ------------------------------------------------------------------ P5: Rate degradation

    def _apply_p5(self, record: dict, tag: str, is_event: bool) -> None:
        # Spec: "Initially, avoid perturbing alarm events in this family because
        # alarm semantics are easier to interpret under P1/P3."
        if is_event:
            self._emit(record, is_event)
            return
        if not self._is_affected(tag):
            self._emit(record, is_event)
            return
        cnt = self._p5_counts.get(tag, 0) + 1
        self._p5_counts[tag] = cnt
        # Keep the first record in each group of q (1-based counter).
        # Use cnt % q == 0 is wrong for q=1 (0%1==0 always True would keep all,
        # but 1%1==0 also True — correct). Use modulo with 0-based offset instead:
        # keep when (cnt - 1) % q == 0, i.e., records 1, 1+q, 1+2q, ...
        if (cnt - 1) % self.spec.q == 0:
            self._emit(record, is_event)
        # else: drop — systematic rate degradation
