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


def test_there_is_no_per_run_spending_cap(tmp_path):
    assert make(tmp_path, max_images_per_job=500).check("gemini", n_images=400) == pytest.approx(0.067 * 400)  # $26.8, not refused


def test_paid_calls_disabled_blocks(tmp_path):
    with pytest.raises(BudgetExceeded) as e:
        make(tmp_path, paid_image_calls_enabled=False).check("gemini")
    assert e.value.code == "paid_disabled"


def test_lifetime_is_persistent_and_never_blocks(tmp_path):
    g = make(tmp_path)
    for _ in range(100):  # 6.70 in total: nothing is refused
        g.check("gemini")
        g.record(artifact_id="a", provider_id="gemini", model_id="m", est_cost_usd=0.067, usage={}, outcome="ok")
    g2 = make(tmp_path)  # a fresh guard (simulating restart) sees the same ledger
    assert g2.spent_usd() == pytest.approx(6.7)
    g2.check("gemini")  # still allowed


def test_planning_and_generation_are_separate_and_total_is_their_sum(tmp_path):
    g = make(tmp_path)
    g.record(artifact_id=None, provider_id="gemini", model_id="t", est_cost_usd=0.0123, usage={"kind": "text_plan"}, outcome="ok:plan", kind="plan")
    g.record(artifact_id="a1", provider_id="gemini", model_id="m", est_cost_usd=0.067, usage={}, outcome="ok", job_id="job-1")
    g.record(artifact_id=None, provider_id="gemini", model_id="m", est_cost_usd=0.067, usage={}, outcome="failed:x", kind="image", job_id="job-1")
    lt = make(tmp_path).lifetime()  # re-read from disk
    assert lt["planning_usd"] == pytest.approx(0.0123) and lt["generation_usd"] == pytest.approx(0.134)
    assert lt["total_usd"] == pytest.approx(lt["planning_usd"] + lt["generation_usd"])


def test_legacy_entries_are_classified_without_rewriting_the_ledger(tmp_path):
    import json
    p = tmp_path / "ledger.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in (
        {"artifact_id": None, "est_cost_usd": 0.01, "usage": {"kind": "text_plan"}, "outcome": "ok:plan"},
        {"artifact_id": "art-1", "est_cost_usd": 0.067, "usage": {}, "outcome": "ok"})) + "\n", encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    g = make(tmp_path)
    assert g.lifetime()["planning_usd"] == pytest.approx(0.01) and g.lifetime()["generation_usd"] == pytest.approx(0.067)
    per = g.by_job({"art-1": "job-9"})  # image attributed through its artifact; the planning call is NOT guessed
    assert per["job-9"]["generation_usd"] == pytest.approx(0.067) and per["job-9"]["planning_usd"] == 0
    assert per["__unattributed__"]["planning_usd"] == pytest.approx(0.01)
    assert p.read_text(encoding="utf-8") == before


def test_attached_planning_cost_is_counted_once(tmp_path):
    g = make(tmp_path)
    eid = g.record(artifact_id=None, provider_id="gemini", model_id="t", est_cost_usd=0.02, usage={}, outcome="ok:plan", kind="plan")
    g.attach(eid, "job-1")
    assert g.lifetime()["total_usd"] == pytest.approx(0.02)
    assert g.by_job({})["job-1"]["planning_usd"] == pytest.approx(0.02) and g.by_job({})["__unattributed__"]["planning_usd"] == 0


def test_max_images_per_job(tmp_path):
    with pytest.raises(BudgetExceeded) as e:
        make(tmp_path, max_images_per_job=5).check("gemini", n_images=6)
    assert e.value.code == "too_many_images"


def test_summary_has_planning_generation_total(tmp_path):
    g = make(tmp_path)
    g.record(artifact_id="a", provider_id="gemini", model_id="m", est_cost_usd=0.25, usage={}, outcome="ok")
    g.record(artifact_id=None, provider_id="gemini", model_id="t", est_cost_usd=0.05, usage={}, outcome="ok:plan")
    s = g.summary()
    assert s.generation_estimated_usd == pytest.approx(0.25) and s.planning_estimated_usd == pytest.approx(0.05)
    assert s.spent_estimated_usd == pytest.approx(0.30) and "no application-side spending cap" in s.note
