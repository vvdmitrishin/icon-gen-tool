import pytest

from app.config import Settings
from app.services.cost_guard import BudgetExceeded, CostGuard


def make(tmp_path, **kw):
    base = dict(data_root=tmp_path, gemini_api_key="k")
    base.update(kw)
    return CostGuard(Settings(**base))


def test_mock_and_free_are_zero(tmp_path):
    g = make(tmp_path)
    assert g.check("mock") == 0.0


def test_gemini_estimate_and_allowed(tmp_path):
    g = make(tmp_path)
    assert g.check("gemini") == pytest.approx(0.067)


def test_job_cap_blocks(tmp_path):
    with pytest.raises(BudgetExceeded) as e:
        make(tmp_path, job_budget_usd=0.01).check("gemini")
    assert e.value.code == "job_cap"


def test_paid_calls_disabled_blocks(tmp_path):
    with pytest.raises(BudgetExceeded) as e:
        make(tmp_path, paid_image_calls_enabled=False).check("gemini")
    assert e.value.code == "paid_disabled"


def test_total_cap_uses_persistent_ledger(tmp_path):
    g = make(tmp_path, total_budget_usd=0.15)
    for _ in range(2):
        g.check("gemini")
        g.record(artifact_id="a", provider_id="gemini", model_id="m",
                 est_cost_usd=0.067, usage={}, outcome="ok")
    # a fresh guard (simulating restart) sees the same ledger
    g2 = make(tmp_path, total_budget_usd=0.15)
    assert g2.spent_usd() == pytest.approx(0.134)
    with pytest.raises(BudgetExceeded) as e:
        g2.check("gemini")
    assert e.value.code == "total_cap"


def test_max_images_per_job(tmp_path):
    with pytest.raises(BudgetExceeded) as e:
        make(tmp_path, max_images_per_job=5).check("gemini", n_images=6)
    assert e.value.code == "too_many_images"


def test_summary_remaining(tmp_path):
    g = make(tmp_path, total_budget_usd=1.0)
    g.record(artifact_id="a", provider_id="gemini", model_id="m",
             est_cost_usd=0.25, usage={}, outcome="ok")
    s = g.summary()
    assert s.remaining_estimated_usd == pytest.approx(0.75)
    assert "not a hard limit" in s.note
