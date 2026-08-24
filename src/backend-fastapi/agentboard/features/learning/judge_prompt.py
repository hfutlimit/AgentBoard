"""L3 LLM-as-judge rubric 与 prompt 模板（Epic 140 切片 2）。

打分维度（Story 267 §关键设计 L3 产出质量）：
- spec_coverage: 需求覆盖度（交付是否覆盖 spec/description 声明的要点）
- code_quality:   代码质量（可读性 / 结构 / 是否遗留 TODO / 是否有明显反模式）
- test_coverage:  测试覆盖（是否含配套测试；关键路径是否被覆盖）
- spec_drift:     需求漂移（终态是否与最初 spec 一致；withdrawn/blocked 是否合理）
- reason_quality: 理由质量（status_reason / 评论中的说明是否真实、具体、可验证）

judge_quality = 五维均值（0~1）。复合分公式见 service.W_* 权重。
"""
from __future__ import annotations

# L3 子维度键（judge 输出 JSON 必须包含，schema 校验依据）
JUDGE_KEYS = [
    "spec_coverage",
    "code_quality",
    "test_coverage",
    "spec_drift",
    "reason_quality",
]

SYSTEM_PROMPT = """你是一名严格的软件交付评审员（LLM-as-judge），为 AgentBoard 平台中 AI Agent 完成的任务打分。
你的职责：基于任务的 spec、实现说明、状态流转、评审评论等证据，对交付质量给出 0~1 的小数评分。

打分规则：
1. 只依据输入提供的证据评分，禁止脑补输入中不存在的信息。
2. spec_coverage：实现说明中明确覆盖的 spec 要点越多分越高；未提及 spec 的视为未覆盖。
3. code_quality：提到关键实现细节、结构清晰、无遗留 TODO/半成品迹象则高；只写"完成"没有细节则低。
4. test_coverage：明确提到测试/验证（pytest、E2E、回归、冒烟）则高；完全没有验证证据则低。
5. spec_drift：终态与初始 spec 一致（completed 且过程无异常）则高；withdrawn/blocked 需理由充分才不算漂移。
6. reason_quality：status_reason 与评论中的说明真实、具体、可验证则高；空泛、模板化、自相矛盾则低。
7. 反偏见：对简短但完整的回答给满分，不因篇幅短而扣分；对冗长空洞的回答严格扣分。
8. 所有维度必须是 0~1 之间的小数（保留 2 位小数）。

输出必须是严格 JSON（无 markdown 代码块、无额外文字）：
{
  "spec_coverage": 0.0~1.0,
  "code_quality": 0.0~1.0,
  "test_coverage": 0.0~1.0,
  "spec_drift": 0.0~1.0,
  "reason_quality": 0.0~1.0,
  "judge_quality": 五个维度的算术平均,
  "rationale": "不超过 120 字的中文评审依据，列出扣分/加分的关键证据"
}"""

USER_PROMPT_TEMPLATE = """请评审以下 Agent 任务交付：

## 任务
- 标题：{title}
- 类型：{task_type}
- 终态：{status}（status_reason: {status_reason}）
- 优先级：{priority}
- 标签：{labels}

## 需求（spec / description）
{spec}

## 状态流转历史（from → to）
{transitions}

## 过程指标（L1/L2 纯统计）
{metrics}

## 评论（评审/协作往返，含作者与时间）
{comments}

## 待打分维度
{judge_keys}

请输出严格 JSON。"""


def build_user_prompt(
    *, title: str, task_type: str, status: str, status_reason: str | None,
    priority: str, labels: str, spec: str, transitions: str,
    metrics: str, comments: str,
) -> str:
    """组装用户侧 prompt（judge 输入序列化由调用方负责）。"""
    return USER_PROMPT_TEMPLATE.format(
        title=title or "(无标题)",
        task_type=task_type or "dev",
        status=status or "unknown",
        status_reason=status_reason or "(无)",
        priority=priority or "medium",
        labels=labels or "[]",
        spec=spec or "(无 spec/description)",
        transitions=transitions or "(无状态流转记录)",
        metrics=metrics or "{}",
        comments=comments or "(无评论)",
        judge_keys=", ".join(JUDGE_KEYS),
    )
