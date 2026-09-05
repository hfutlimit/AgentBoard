/** Display legacy Worker JSON comments as Markdown without changing stored evidence. */
const labels: Record<string, string> = {
  summary: '结论', tests_passed: '验收是否通过', deployment_steps: '部署记录', test_steps: '测试步骤',
  test_results: '测试结果', defects: '问题与阻塞', artifacts: '交付文件', evidence: '证据',
  validation: '检查记录', design_document: '设计文档', design_document_id: '设计文档编号',
  design_document_url: '设计文档链接', commit: '代码提交', agent_id: '执行 Agent', provider: '运行工具',
  model: '模型', title: '标题', content: '正文', description: '说明', notable_contract: '契约说明',
  source_work_id: '来源工作', bug_task_ids: '缺陷任务', retest_task_id: '复测任务',
};
const decisions: Record<string, string> = {
  submit: '提交评审', approve: '评审通过', discuss: '待讨论', respond: '回复评审', confirm: '确认',
  withdraw: '撤回', escalate: '需人工裁决',
};

function valueMarkdown(value: unknown, depth = 4): string {
  if (value === null) return '未提供';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (Array.isArray(value)) return value.map((item, i) => item !== null && typeof item === 'object'
    ? `${'#'.repeat(Math.min(depth + 1, 6))} 第 ${i + 1} 项\n\n${valueMarkdown(item, depth + 2)}`
    : `${i + 1}. ${valueMarkdown(item, depth)}`).join('\n\n') || '无';
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${'#'.repeat(Math.min(depth, 6))} ${Object.hasOwn(labels, key) ? labels[key] : key}\n\n${valueMarkdown(item, depth + 1)}`).join('\n\n') || '无';
  return String(value);
}

export function workerCommentMarkdown(source: string): string {
  if (!source?.trimStart().startsWith('{')) return source;
  let result: Record<string, unknown>;
  try { result = JSON.parse(source); } catch { return source; }
  // Do not reinterpret arbitrary user JSON or normal Markdown/code examples.
  if (!result || Array.isArray(result) || typeof result['agent_id'] !== 'string'
      || typeof result['summary'] !== 'string' || typeof result['decision'] !== 'string'
      || !Object.hasOwn(decisions, result['decision'])) return source;
  const role = Object.hasOwn(result, 'tests_passed') ? 'QA' : Object.hasOwn(result, 'artifacts') || Object.hasOwn(result, 'design_document') ? '设计' : '执行';
  const parts = [`### ${role}结果 · ${decisions[result['decision']]}`, result['summary']];
  const keys = ['tests_passed', 'deployment_steps', 'test_steps', 'test_results', 'defects',
    'artifacts', 'design_document_url', 'validation', 'evidence'];
  keys.push(...Object.keys(result).filter(key => !keys.includes(key)));
  for (const key of keys) {
    if (key === 'summary' || key === 'decision' || !Object.hasOwn(result, key) || result[key] === null || result[key] === '') continue;
    parts.push(`#### ${Object.hasOwn(labels, key) ? labels[key] : key}\n\n${valueMarkdown(result[key])}`);
  }
  return parts.join('\n\n');
}
