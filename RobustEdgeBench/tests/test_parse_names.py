from pathlib import Path

from robustedge.io import parse_scenario_name


def test_parse_current_campaign_name():
    p = Path("phase-phase4_perturbed_attacked_perturbation-P2_lam0.50_attackDuration-20_intensity-medium_20260426T043520Z") / "iteration-3"
    meta = parse_scenario_name(p)
    assert meta["phase"] == "phase4_perturbed_attacked"
    assert meta["perturbation_family"] == "P2"
    assert meta["severity"] == 0.50
    assert meta["attack_duration"] == 20
    assert meta["attack_intensity"] == "medium"
    assert meta["iteration"] == 3
