from src.model_d.evaluation import EvaluationObservation, chronological_oos_report


def test_oos_report_deduplicates_and_separates_long_short():
    rows = [EvaluationObservation(f'id-{i}', i, 'LONG' if i % 2 == 0 else 'SHORT', 1.0 if i % 2 == 0 else -0.5, 'binance_orderflow', 'binance', i) for i in range(40)]
    rows.append(rows[-1])
    report = chronological_oos_report(rows, min_test_sample=3)
    assert report['duplicate_count'] == 1
    assert report['strategies']['binance_orderflow']['LONG']['sample_size'] == 4
    assert report['strategies']['binance_orderflow']['SHORT']['sample_size'] == 4


def test_oos_report_is_insufficient_without_data():
    report = chronological_oos_report([], min_test_sample=3)
    assert report['status'] == 'INSUFFICIENT_DATA'
