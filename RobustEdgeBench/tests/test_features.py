from pathlib import Path

from robustedge.data import DatasetIndex
from robustedge.features import MultiViewFeatureBuilder, infer_feature_columns


def test_fused_features_from_minimal_dataset():
    data_root = Path(__file__).parent / "fixtures" / "minimal_dataset"
    runs = DatasetIndex.from_root(data_root).load_runs()
    features = MultiViewFeatureBuilder().transform_runs(runs)
    cols = infer_feature_columns(features, prefixes=("rt_", "proc_", "ctrl_"))
    assert not features.empty
    assert any(c.startswith("rt_") for c in cols)
    assert any(c.startswith("proc_") for c in cols)
    assert any(c.startswith("ctrl_") for c in cols)
    assert "phase" in features.columns
    assert "label" in features.columns
