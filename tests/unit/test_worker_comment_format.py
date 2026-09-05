from agentboard.features.work_items.comment_format import format_worker_comment


def test_failed_qa_comment_preserves_evidence_without_json():
    payload = dict(agent_id="qa", decision="submit", summary="环境阻塞", tests_passed=False,
                   deployment_steps=["启动前端"], test_results=["82 passed"],
                   defects=[dict(title="浏览器不可用", description="未执行点击；证据 `a.log`")],
                   model="model-x", custom=dict(details="附加证据"), memory_citation="")
    markdown = format_worker_comment(payload, "qa")
    assert markdown.startswith("### QA结果 · 提交评审")
    assert "#### 验收是否通过\n\n否" in markdown
    for text in ("启动前端", "82 passed", "浏览器不可用", "`a.log`", "model-x", "附加证据"):
        assert text in markdown
    assert '"tests_passed"' not in markdown
    assert "1. ####" not in markdown
    assert "memory_citation" not in markdown
    assert payload["tests_passed"] is False


def test_dev_and_design_keep_summary_and_artifacts():
    assert "开发结果" in format_worker_comment(dict(summary="完成", decision="submit"), "dev")
    markdown = format_worker_comment(dict(summary="设计完成", artifacts=["design.md"],
                                        validation=dict(check=True)), "design")
    assert "设计结果" in markdown
    assert "design.md" in markdown and "是" in markdown
