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


def test_schedule_proposal_questions_runs_in_background(monkeypatch):
    """P2-10: when the FastAPI handler hands a BackgroundTasks instance
    to ``schedule_proposal_questions``, the SignalR bridge call must
    not run inline — it must be queued for after the response is sent,
    so the proposal request does not block on the .NET BFF.
    """
    calls: list[tuple[int, int, int]] = []

    def fake_post(url, *, headers, json, timeout):
        calls.append((json["proposal_id"], json["project_id"], json["round"]))
        class R:
            is_error = False
            status_code = 202
        return R()

    monkeypatch.setenv("AGENTBOARD_REALTIME_NOTIFY_URL", "http://bff/api/internal/realtime/proposals/questions")
    monkeypatch.setenv("AGENTBOARD_REALTIME_INTERNAL_KEY", "test-key")
    monkeypatch.setattr(realtime.httpx, "post", fake_post)

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    realtime.schedule_proposal_questions(
        bg, proposal_id=99, project_id=4, round_no=2,
    )

    # Before awaiting the background tasks queue, no HTTP call has been
    # made yet — the original sync version would have called by now.
    assert calls == []
    # Run the queued background task and verify it lands.
    import asyncio
    asyncio.run(bg())
    assert calls == [(99, 4, 2)]


def test_schedule_proposal_questions_falls_back_to_sync_without_background(monkeypatch):
    """Outside the request scope, ``schedule_proposal_questions`` must
    still send the notification (synchronously) so that worker scripts
    do not silently drop the bridge call.
    """
    calls: list[dict] = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json)
        class R:
            is_error = False
            status_code = 202
        return R()

    monkeypatch.setenv("AGENTBOARD_REALTIME_NOTIFY_URL", "http://bff/x")
    monkeypatch.setenv("AGENTBOARD_REALTIME_INTERNAL_KEY", "k")
    monkeypatch.setattr(realtime.httpx, "post", fake_post)

    realtime.schedule_proposal_questions(
        None, proposal_id=7, project_id=3, round_no=1,
    )
    assert calls == [{
        "proposal_id": 7,
        "project_id": 3,
        "round": 1,
        "workflow": "goal",
        "event": "proposal.questions_raised",
    }]
