"""#19 the planner learns per-signal trust from the owner's confirm() answers."""
import pytest

from schemas import BrandDNA, Suspect


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    import prarthana.learning as learning
    import prarthana.relaunch as relaunch
    import prarthana.alert as alert
    monkeypatch.setattr(learning, "CACHE", tmp_path)
    monkeypatch.setattr(relaunch, "CACHE", tmp_path)
    monkeypatch.setattr(alert, "OUTPUT", tmp_path)
    return tmp_path


def _site(i, signals):
    return {"url": f"https://s{i}.example/", "domain": f"s{i}.example", "signals": signals}


def test_not_fake_answers_lower_weight_with_floor():
    from prarthana.learning import compute_weights
    fb = [{**_site(i, ["contacts"]), "answer": "not_fake"} for i in range(3)]
    w = compute_weights(fb)
    assert w["contacts"] == 0.7 and w["photos"] == 1.0
    many = [{**_site(i, ["contacts"]), "answer": "not_fake"} for i in range(20)]
    assert compute_weights(many)["contacts"] == 0.5          # never below half


def test_fake_answers_raise_weight_with_ceiling():
    from prarthana.learning import compute_weights
    fb = [{**_site(i, ["photos", "logo"]), "answer": "fake"} for i in range(4)]
    w = compute_weights(fb)
    assert w["photos"] == 1.2 and w["logo"] == 1.2 and w["price"] == 1.0
    many = [{**_site(i, ["photos"]), "answer": "fake"} for i in range(40)]
    assert compute_weights(many)["photos"] == 1.5            # never above 1.5x


def test_mixed_answers_and_no_history():
    from prarthana.learning import compute_weights
    assert set(compute_weights([]).values()) == {1.0}
    fb = [{**_site(1, ["contacts"]), "answer": "fake"}, {**_site(2, ["contacts"]), "answer": "not_fake"}]
    assert compute_weights(fb)["contacts"] == 0.95


def test_confirm_records_signals_and_scorer_uses_weights(cache):
    from prarthana.alert import confirm
    from prarthana.learning import compute_weights, load_feedback
    from prarthana.scorer import score_suspect
    dna = BrandDNA(store_url="https://brand.example/", official_contact_hashes=[])
    legit = []
    for i in range(3):   # three legit shops that only list their own contacts (15 points each)
        s = Suspect(url=f"https://shop{i}.example/", contact_hashes=[f"h{i}"], contacts_masked=["98XXXXXX10"])
        score_suspect(s, dna)
        assert s.signals_fired == ["contacts"] and s.score == 15
        confirm(s, False)
    fb = load_feedback()
    assert [x["answer"] for x in fb] == ["not_fake"] * 3 and fb[0]["signals"] == ["contacts"]
    confirm(Suspect(url="https://shop0.example/", contact_hashes=["h0"], signals_fired=["contacts"]), False)
    assert len(load_feedback()) == 3                          # latest answer per URL wins
    w = compute_weights()
    s = Suspect(url="https://new.example/", contact_hashes=["z"], contacts_masked=["99XXXXXX99"])
    score_suspect(s, dna, w)
    assert s.score == 10 and s.reasons[0].startswith("+10: lists 1")   # 15 * 0.70 = 10.5 -> 10
    assert s.signal_weights["contacts"] == 0.7
