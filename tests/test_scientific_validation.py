from scripts.audit_h001 import audit
from scripts.synthetic_validation import evaluate


def test_h001_original_and_retrospective_remain_separate():
    report = audit()
    assert report["original_n_trials"] == 1
    assert report["corrected_n_configurations"] == 3
    assert report["original_final_dsr"] > report["corrected_historical_formula_dsr"]
    assert (
        report["independent_calculation_dsr"]
        == report["corrected_historical_formula_dsr"]
    )


def test_deterministic_scientific_suite():
    report = evaluate()
    assert report["true_discoveries"] == 1
    assert report["false_promotions"] == 0
    assert report["rejections"] == 3
