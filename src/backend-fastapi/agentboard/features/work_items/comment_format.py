"""Human-readable Worker comments; structured execution evidence stays in WorkerWork.result."""

LABELS = {
    "summary": "结论", "tests_passed": "验收是否通过", "deployment_steps": "部署记录",
    "test_steps": "测试步骤", "test_results": "测试结果", "defects": "问题与阻塞",
    "artifacts": "交付文件", "evidence": "证据", "validation": "检查记录",
    "design_document": "设计文档", "design_document_id": "设计文档编号",
    "design_document_url": "设计文档链接", "commit": "代码提交", "agent_id": "执行 Agent",
    "provider": "运行工具", "model": "模型", "title": "标题", "content": "正文",
    "description": "说明", "notable_contract": "契约说明", "source_work_id": "来源工作",
    "bug_task_ids": "缺陷任务", "retest_task_id": "复测任务",
}
DECISIONS = {"submit": "提交评审", "approve": "评审通过", "discuss": "待讨论",
             "respond": "回复评审", "confirm": "确认", "withdraw": "撤回", "escalate": "需人工裁决"}


def _value(value, depth=4):
    if value is None:
        return "未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "\n\n".join(
            f"{'#' * min(depth + 1, 6)} 第 {index} 项\n\n{_value(item, depth + 2)}"
            if isinstance(item, (dict, list)) else f"{index}. {_value(item, depth)}"
            for index, item in enumerate(value, 1)) or "无"
    if isinstance(value, dict):
        return "\n\n".join(f"{'#' * min(depth, 6)} {LABELS.get(key, key)}\n\n{_value(item, depth + 1)}"
                           for key, item in value.items()) or "无"
    return str(value)


def format_worker_comment(result: dict, kind: str) -> str:
    role = {"design": "设计", "dev": "开发", "qa": "QA"}.get(kind, "执行")
    decision = result.get("decision", "submit")
    parts = [f"### {role}结果 · {DECISIONS.get(decision, decision)}"]
    if result.get("summary"):
        parts.append(_value(result["summary"]))
    # Stable business-first order. Preserve extension fields as Markdown too.
    keys = ["tests_passed", "deployment_steps", "test_steps", "test_results", "defects",
            "artifacts", "design_document_url", "validation", "evidence"]
    keys += [key for key in result if key not in keys]
    for key in keys:
        if key in ("summary", "decision") or key not in result or result[key] is None or result[key] == "":
            continue
        parts.append(f"#### {LABELS.get(key, key)}\n\n{_value(result[key])}")
    return "\n\n".join(parts)
