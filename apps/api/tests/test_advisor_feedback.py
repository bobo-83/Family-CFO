"""ADR 0044: member 👍/👎 on advisor answers, distilled by the study job."""

import pytest
from sqlalchemy.engine import Engine

from family_cfo_ai_orchestrator import RuntimeCompletion

from family_cfo_api import ai_study, fixtures, repository


def _make_recommendation(engine: Engine, answer: str = "Here is the plan.") -> str:
    return repository.create_recommendation(
        engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        scenario_id=None,
        answer=answer,
        assumptions=[],
        impacts=[],
        tradeoffs=[],
        alternatives=[],
        confidence=0.9,
        calculation_refs=[],
        warnings=[],
        explanation_source="agentic_tool_calling",
    )


class _StubRuntime:
    def __init__(self, text: str):
        self._text = text
        self.seen: list = []

    def complete(self, messages, *, temperature=0.2, max_tokens=400):
        self.seen.append(list(messages))
        return RuntimeCompletion(text=self._text, model="stub", raw={})

    def close(self):
        pass


@pytest.mark.anyio
async def test_feedback_requires_authentication(demo_client) -> None:
    resp = await demo_client.post(
        "/api/v1/chat/feedback", json={"recommendation_id": "x", "rating": "up"}
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_feedback_on_foreign_recommendation_is_404(demo_client, demo_token) -> None:
    resp = await demo_client.post(
        "/api/v1/chat/feedback",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"recommendation_id": "99999999-0000-0000-0000-000000000000", "rating": "down"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_feedback_is_recorded_and_reratable(demo_client, demo_token, demo_engine) -> None:
    rec_id = _make_recommendation(demo_engine)
    headers = {"Authorization": f"Bearer {demo_token}"}

    down = await demo_client.post(
        "/api/v1/chat/feedback",
        headers=headers,
        json={"recommendation_id": rec_id, "rating": "down", "note": "you ignored my RSUs"},
    )
    assert down.status_code == 204

    pending = repository.list_unreviewed_feedback(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert [(f.recommendation_id, f.rating, f.note) for f in pending] == [
        (rec_id, "down", "you ignored my RSUs")
    ]

    # Re-rating the same answer updates in place (one row, new rating).
    up = await demo_client.post(
        "/api/v1/chat/feedback",
        headers=headers,
        json={"recommendation_id": rec_id, "rating": "up"},
    )
    assert up.status_code == 204
    pending = repository.list_unreviewed_feedback(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert len(pending) == 1 and pending[0].rating == "up"


def test_review_feedback_distills_a_lesson_and_marks_reviewed(demo_engine: Engine) -> None:
    rec_id = _make_recommendation(demo_engine, answer="Your income is $X.")
    repository.upsert_advisor_feedback(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, rec_id, fixtures.DEMO_USER_ID, "down",
        "you keep forgetting my RSU income",
    )
    runtime = _StubRuntime(
        '[{"key": "advisor_include_rsu_income", "value": "Always include RSU vests when '
        'estimating this household\'s income."}]'
    )

    reviewed = ai_study.review_feedback(runtime, demo_engine, fixtures.DEMO_HOUSEHOLD_ID)

    assert reviewed == 1
    # The lesson is now household knowledge, injected into every future chat.
    insights = {m.key: m.value for m in repository.list_study_insights(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)}
    assert "advisor_include_rsu_income" in insights
    # And the feedback is no longer pending.
    assert repository.list_unreviewed_feedback(demo_engine, fixtures.DEMO_HOUSEHOLD_ID) == []
    # The runtime saw the answer and the note.
    assert "RSU income" in runtime.seen[0][-1].content
