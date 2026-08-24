from agentboard import realtime


def test_goal_proposal_question_notification_contains_refresh_coordinates(monkeypatch):
    calls = []

    class Response:
        is_error = False
        status_code = 202

    def fake_post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return Response()

    monkeypatch.setenv("AGENTBOARD_REALTIME_NOTIFY_URL", "http://bff/api/internal/realtime/proposals/questions")
    monkeypatch.setenv("AGENTBOARD_REALTIME_INTERNAL_KEY", "test-key")
    monkeypatch.setattr(realtime.httpx, "post", fake_post)

    realtime.notify_proposal_questions(proposal_id=12, project_id=7, round_no=3)

    assert calls == [(
        "http://bff/api/internal/realtime/proposals/questions",
        {"X-AgentBoard-Realtime-Key": "test-key"},
        {
            "proposal_id": 12,
            "project_id": 7,
            "round": 3,
            "workflow": "goal",
            "event": "proposal.questions_raised",
        },
        2.0,
    )]
