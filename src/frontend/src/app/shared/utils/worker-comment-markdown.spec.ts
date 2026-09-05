import { describe, expect, it } from 'vitest';
import { workerCommentMarkdown } from './worker-comment-markdown';

describe('Worker comments', () => {
  it('formats failed QA with all evidence and attribution', () => {
    const result = workerCommentMarkdown(JSON.stringify({ agent_id: 'qa-agent', decision: 'submit',
      summary: '测试未通过', tests_passed: false, deployment_steps: ['启动成功'], test_results: ['82 passed'],
      defects: [{ title: '浏览器不可用', description: '未执行点击，保留证据 `a.log`。' }], model: 'model-x',
      custom: { observation: '额外证据' }, memory_citation: '' }));
    expect(result).toContain('### QA结果 · 提交评审');
    expect(result).toContain('#### 验收是否通过\n\n否');
    for (const text of ['启动成功', '82 passed', '浏览器不可用', '`a.log`', 'qa-agent', 'model-x', '额外证据']) expect(result).toContain(text);
    expect(result).not.toContain('"tests_passed"');
    expect(result).not.toContain('1. ####');
    expect(result).not.toContain('memory_citation');
  });
  it('preserves normal Markdown, malformed JSON and unrelated JSON exactly', () => {
    for (const text of ['### 讨论\n\n同意', '{broken', '{"summary":"配置","data":1}',
      '```json\n{"agent_id":"a","decision":"submit","summary":"example"}\n```']) {
      expect(workerCommentMarkdown(text)).toBe(text);
    }
  });
  it('preserves HTML as text for the existing sanitized renderer', () => {
    expect(workerCommentMarkdown('{"agent_id":"a","summary":"<img src=x onerror=alert(1)>","decision":"submit"}'))
      .toContain('<img src=x onerror=alert(1)>');
  });
});
