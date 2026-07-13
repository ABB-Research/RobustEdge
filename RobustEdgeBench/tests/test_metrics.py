import numpy as np

from robustedge.metrics import event_recall_and_ttd, false_alarms_per_hour


def test_event_recall_and_ttd():
    times = np.array([0, 4, 8, 12, 16])
    pred = np.array([0, 0, 0, 1, 0])
    er, ttd = event_recall_and_ttd(pred, times, [(10, 18)])
    assert er == 1.0
    assert ttd == [2.0]


def test_false_alarms_per_hour():
    y = np.array([0, 0, 0, 1])
    p = np.array([0, 1, 0, 1])
    assert false_alarms_per_hour(y, p, 4.0) == 1 / (12 / 3600)
