"""WorkflowRun feature package (Epic: Active Workflows Overview).

Slice 1 (2026-09-11):
- Models: WorkflowRun + WorkflowRunEvent
- State machine: WORKFLOW_RUN_TRANSITIONS + WORKFLOW_PHASE_TRANSITIONS
- DB migration: workflow_runs + workflow_run_events

Subsequent slices add:
- Slice 2: emit_workflow_event helper + service.set_status hooks
- Slice 3: agent_runs integration
- Slice 4: 4 read APIs (active-workflows / detail / events / executions)
- Slice 5: Angular UI
- Slice 6: SignalR workflow.changed
- Slice 7: reconciliation + full E2E
"""
