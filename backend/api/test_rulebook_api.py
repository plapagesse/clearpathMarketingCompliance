"""Rulebook API tests: the read view over the real rulebook, and proposals.

The GET assertions run against the shipped rulebook/ on purpose — the page's
whole value is that it shows what the engine actually loads, so a test against a
hand-built fixture would check nothing worth checking.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A Flask test client bound to an empty, isolated database."""
    monkeypatch.setenv("CLEARPATH_DB", f"sqlite:///{tmp_path / 'rulebook-test.db'}")

    from backend.db import session as session_mod

    importlib.reload(session_mod)
    session_mod.init_db(reset=True)

    from backend.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# --------------------------------------------------------------------------- #
# GET /api/rulebook
# --------------------------------------------------------------------------- #


def test_rulebook_reports_version_rules_and_claim_types(client):
    body = client.get("/api/rulebook").get_json()

    assert body["version"]  # whatever manifest.json currently pins
    assert {"version", "claim_types", "rules"} == set(body)

    from backend.engine.checker import load_rulebook

    from backend.api.rulebook_api import RULEBOOK_DIR

    assert len(body["rules"]) == len(load_rulebook(RULEBOOK_DIR).entries)


def test_every_rule_is_readable_by_a_non_engineer(client):
    """rule_id, product, severity, kind, a plain-English line, and an authority."""
    rules = client.get("/api/rulebook").get_json()["rules"]

    for rule in rules:
        assert set(rule) == {
            "rule_id",
            "product",
            "severity",
            "kind",
            "description",
            "citation",
            "url",
        }
        assert rule["product"] in {"personal_loan", "credit_card", "mortgage_prequal"}
        assert rule["severity"] in {"info", "low", "medium", "high", "critical"}
        assert rule["kind"] in {"deterministic", "llm_judged"}
        # The description is the point: no rule may be a bare id on the page.
        assert rule["description"].strip()
        assert " — " in rule["citation"]  # "body — pinpoint cite"
        assert rule["url"].startswith("http")

    assert {r["kind"] for r in rules} == {"deterministic", "llm_judged"}


def test_deterministic_description_is_the_check_description(client):
    """Deterministic rules carry a written plain-English line; we surface it verbatim."""
    from backend.engine.checker import load_rulebook

    from backend.api.rulebook_api import RULEBOOK_DIR

    entries = {e.rule_id: e for e in load_rulebook(RULEBOOK_DIR).entries}
    rules = {r["rule_id"]: r for r in client.get("/api/rulebook").get_json()["rules"]}

    deterministic = next(r for r in rules.values() if r["kind"] == "deterministic")
    assert deterministic["description"] == entries[deterministic["rule_id"]].parameters[
        "check_description"
    ]


def test_judged_description_is_the_first_sentence_of_judge_focus(client):
    from backend.engine.checker import load_rulebook

    from backend.api.rulebook_api import RULEBOOK_DIR

    entries = {e.rule_id: e for e in load_rulebook(RULEBOOK_DIR).entries}
    rules = client.get("/api/rulebook").get_json()["rules"]

    judged = next(r for r in rules if r["kind"] == "llm_judged")
    focus = entries[judged["rule_id"]].parameters["judge_focus"]
    assert focus.startswith(judged["description"])
    assert len(judged["description"]) <= len(focus)


def test_claim_types_carry_definitions_and_typed_fields(client):
    claim_types = client.get("/api/rulebook").get_json()["claim_types"]

    names = [c["name"] for c in claim_types]
    assert "triggering_term" in names and "rate_or_apr" in names
    assert all(c["definition"].strip() for c in claim_types)

    rate = next(c for c in claim_types if c["name"] == "rate_or_apr")
    fields = {f["name"]: f for f in rate["fields"]}
    assert fields["value_pct"]["type"] == "number"
    assert fields["value_pct"]["optional"] is True
    # closed vocabularies come through so the page can show what a field may hold
    assert fields["rate_kind"]["values"] == ["apr", "interest_rate", "unlabeled"]
    assert "values" not in fields["value_pct"]


# --------------------------------------------------------------------------- #
# proposals
# --------------------------------------------------------------------------- #


def _proposal(**overrides) -> dict:
    body = {
        "product": "credit_card",
        "title": "Flag 'no interest' without deferred-interest terms",
        "description": "Any 'no interest' claim must state the deferred-interest terms nearby.",
        "severity": "high",
        "citation_url": "https://www.consumerfinance.gov/rules-policy/regulations/1026/16/",
        "rationale": "Two partner creatives last month used it with no terms in sight.",
    }
    body.update(overrides)
    return body


def test_proposal_round_trips_as_pending(client):
    assert client.get("/api/rulebook/proposals").get_json() == []

    created = client.post("/api/rulebook/proposals", json=_proposal())
    assert created.status_code == 201
    row = created.get_json()
    assert row["id"].startswith("rp-")
    assert row["status"] == "pending"
    assert row["product"] == "credit_card"
    assert row["severity"] == "high"
    assert row["created_at"]

    listed = client.get("/api/rulebook/proposals").get_json()
    assert [p["id"] for p in listed] == [row["id"]]
    assert listed[0] == row


def test_proposals_never_leak_into_the_rulebook(client):
    """A proposal is a request, not a rule: the engine's rule list is untouched."""
    before = len(client.get("/api/rulebook").get_json()["rules"])

    client.post("/api/rulebook/proposals", json=_proposal())

    assert len(client.get("/api/rulebook").get_json()["rules"]) == before


def test_proposal_validates_product_title_and_severity(client):
    assert client.post("/api/rulebook/proposals", json=_proposal(product="car_loan")).status_code == 400
    assert client.post("/api/rulebook/proposals", json=_proposal(title="  ")).status_code == 400
    assert client.post("/api/rulebook/proposals", json=_proposal(severity="urgent")).status_code == 400
    assert client.post("/api/rulebook/proposals", json={}).status_code == 400

    assert client.get("/api/rulebook/proposals").get_json() == []


def test_optional_fields_may_be_omitted(client):
    resp = client.post(
        "/api/rulebook/proposals",
        json={"product": "personal_loan", "title": "Ban 'guaranteed'", "severity": "critical"},
    )

    assert resp.status_code == 201
    row = resp.get_json()
    assert row["citation_url"] is None
    assert row["description"] == "" and row["rationale"] == ""
