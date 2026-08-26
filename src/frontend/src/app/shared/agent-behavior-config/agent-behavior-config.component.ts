import { Component, Input, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../api.service';
import {
  AgentBehaviorConfigPayload,
  EffectiveBehaviorConfig,
  BehaviorPreviewResponse,
  PreparationBehavior,
  CollaborationBehavior,
  LearningBehavior,
} from '../../models';

@Component({
  selector: 'app-agent-behavior-config',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="behavior-config-container">
      <div class="config-header">
        <h3>Agent 行为配置与纠错学习</h3>
        <p class="subtitle">通过语义化开关定制 Agent 在各环节的执行准则，无需编写复杂的提示词脚本。</p>
      </div>

      <!-- 工作类型选择器 -->
      <div class="work-type-selector">
        <label>生效环节 (WorkType):</label>
        <select [(ngModel)]="selectedWorkType" (change)="loadConfig()">
          <option value="proposal_clarify">需求澄清 (Proposal Clarify)</option>
          <option value="proposal_convert">工单转化 (Proposal Convert)</option>
          <option value="design">架构与技术设计 (Design)</option>
          <option value="implementation">代码实现 (Implementation)</option>
          <option value="qa">质量验收 (QA)</option>
          <option value="review">交叉评审 (Review)</option>
        </select>
        <span class="source-badge" *ngIf="effectiveConfig">
          来源: {{ effectiveConfig.sources.agent_work_type ? 'Agent覆盖' : effectiveConfig.sources.project ? '项目覆盖' : '系统默认' }}
        </span>
      </div>

      <!-- 语义配置表单 -->
      <div class="config-sections" *ngIf="payload">
        <!-- 准备阶段 -->
        <div class="section-card">
          <h4>🚀 准备阶段 (Preparation)</h4>
          <div class="switch-group">
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.preparation.sync_code" (change)="onPayloadChange()" />
              <span class="label-text"><strong>同步最新代码</strong> (工作前 git pull 最新代码)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.preparation.checkout_branch" (change)="onPayloadChange()" />
              <span class="label-text"><strong>切换关联分支</strong> (自动切换到任务绑定的 feature 分支)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.preparation.inspect_code" (change)="onPayloadChange()" />
              <span class="label-text"><strong>审查本地代码</strong> (在发问或修改前检索现有源码模式)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.preparation.read_documents" (change)="onPayloadChange()" />
              <span class="label-text"><strong>查阅关联文档</strong> (阅读需求、设计与架构规范)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.preparation.load_memory" (change)="onPayloadChange()" />
              <span class="label-text"><strong>加载项目经验</strong> (注入本项目历史防坑经验)</span>
            </label>
          </div>
        </div>

        <!-- 协同与留痕 -->
        <div class="section-card">
          <h4>🤝 协同与留痕 (Collaboration)</h4>
          <div class="switch-group">
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.collaboration.read_comments" (change)="onPayloadChange()" />
              <span class="label-text"><strong>阅读历史评论</strong> (执行前获取完整讨论与评审上下文)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.collaboration.leave_summary" (change)="onPayloadChange()" />
              <span class="label-text"><strong>留下工作总结</strong> (完工后自动生成结构化执行总结评论)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.collaboration.reply_to_review" (change)="onPayloadChange()" />
              <span class="label-text"><strong>规范回复审查</strong> (对驳回意见明确 ACCEPTED / CHALLENGED)</span>
            </label>
          </div>
        </div>

        <!-- 纠错与持续学习 -->
        <div class="section-card">
          <h4>🧠 纠错与持续学习 (Learning)</h4>
          <div class="switch-group">
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.learning.accepted_correction" (change)="onPayloadChange()" />
              <span class="label-text"><strong>采纳审查后沉淀教训</strong> (Owner 修复问题后自动提炼可复用规则)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.learning.judgment_reversal" (change)="onPayloadChange()" />
              <span class="label-text"><strong>误判更正后反思沉淀</strong> (Reviewer 误判被纠正后沉淀检查项)</span>
            </label>
            <label class="switch-item">
              <input type="checkbox" [(ngModel)]="payload.learning.qa_defect" (change)="onPayloadChange()" />
              <span class="label-text"><strong>QA 发现缺陷后沉淀</strong> (捕获漏测 bug 沉淀测试用例)</span>
            </label>
          </div>
        </div>

        <!-- 补充指令 -->
        <div class="section-card">
          <h4>📝 补充自定义指令 (Additional Instructions)</h4>
          <textarea
            rows="3"
            class="form-control"
            [(ngModel)]="payload.additional_instructions"
            (ngModelChange)="onPayloadChange()"
            placeholder="例如：本项目禁止使用 any 类型，所有外部 API 调用必须添加超时兜底..."
          ></textarea>
        </div>
      </div>

      <!-- 操作与 Prompt 预览按钮 -->
      <div class="action-bar">
        <button class="btn btn-secondary" (click)="loadPreview()">🔍 实时预览 Prompt</button>
        <button class="btn btn-danger" (click)="resetConfig()">🔄 重置为默认</button>
        <button class="btn btn-primary" (click)="saveConfig()">💾 保存配置</button>
        <span class="save-status" *ngIf="statusMsg">{{ statusMsg }}</span>
      </div>

      <!-- 实时预览区 -->
      <div class="preview-card" *ngIf="previewResult">
        <h4>👁️ 运行时 Prompt 预览 (Preview)</h4>
        <pre class="prompt-view">{{ previewResult.rendered_prompt }}</pre>
      </div>
    </div>
  `,
  styles: [`
    .behavior-config-container { padding: 1.5rem; background: #fff; border-radius: 8px; }
    .config-header h3 { margin: 0 0 0.5rem 0; color: #1e293b; }
    .subtitle { color: #64748b; font-size: 0.9rem; margin-bottom: 1.5rem; }
    .work-type-selector { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.5rem; }
    .work-type-selector select { padding: 0.4rem 0.8rem; border-radius: 4px; border: 1px solid #cbd5e1; }
    .source-badge { background: #e0f2fe; color: #0369a1; padding: 0.2rem 0.6rem; border-radius: 12px; font-size: 0.8rem; font-weight: 500; }
    .config-sections { display: flex; flex-direction: column; gap: 1.2rem; }
    .section-card { border: 1px solid #e2e8f0; border-radius: 6px; padding: 1rem; background: #f8fafc; }
    .section-card h4 { margin: 0 0 0.8rem 0; font-size: 1rem; color: #334155; }
    .switch-group { display: flex; flex-direction: column; gap: 0.6rem; }
    .switch-item { display: flex; align-items: center; gap: 0.6rem; cursor: pointer; font-size: 0.9rem; }
    .form-control { width: 100%; padding: 0.5rem; border: 1px solid #cbd5e1; border-radius: 4px; font-size: 0.9rem; }
    .action-bar { display: flex; align-items: center; gap: 0.8rem; margin-top: 1.5rem; }
    .btn { padding: 0.5rem 1rem; border-radius: 4px; border: none; font-size: 0.9rem; cursor: pointer; font-weight: 500; }
    .btn-primary { background: #2563eb; color: #fff; }
    .btn-secondary { background: #64748b; color: #fff; }
    .btn-danger { background: #ef4444; color: #fff; }
    .save-status { font-size: 0.9rem; color: #16a34a; margin-left: 0.5rem; }
    .preview-card { margin-top: 1.5rem; border: 1px solid #cbd5e1; border-radius: 6px; padding: 1rem; background: #0f172a; color: #f8fafc; }
    .preview-card h4 { margin: 0 0 0.8rem 0; color: #38bdf8; }
    .prompt-view { white-space: pre-wrap; font-family: monospace; font-size: 0.85rem; max-height: 400px; overflow-y: auto; color: #e2e8f0; }
  `]
})
export class AgentBehaviorConfigComponent implements OnInit {
  @Input() projectId!: number;
  @Input() agentId?: number;

  selectedWorkType = 'implementation';
  effectiveConfig: EffectiveBehaviorConfig | null = null;
  payload: any = {
    preparation: { sync_code: true, checkout_branch: false, inspect_code: true, read_documents: true, load_memory: true },
    collaboration: { read_comments: true, leave_summary: true, reply_to_review: true },
    learning: { accepted_correction: true, judgment_reversal: true, qa_defect: true },
    document_sources: [{ type: 'project_documents' }],
    additional_instructions: '',
  };
  previewResult: BehaviorPreviewResponse | null = null;
  statusMsg = '';

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.loadConfig();
  }

  loadConfig(): void {
    if (!this.projectId) return;
    const req = this.agentId
      ? this.api.getAgentBehavior(this.projectId, this.agentId, this.selectedWorkType)
      : this.api.getProjectBehavior(this.projectId, this.selectedWorkType);

    req.subscribe({
      next: (config) => {
        this.effectiveConfig = config;
        this.payload = {
          preparation: { ...config.preparation },
          collaboration: { ...config.collaboration },
          learning: { ...config.learning },
          document_sources: config.document_sources || [],
          additional_instructions: config.additional_instructions || '',
        };
      },
      error: (err) => console.error('Failed to load behavior config', err),
    });
  }

  onPayloadChange(): void {
    this.statusMsg = '';
  }

  saveConfig(): void {
    if (!this.projectId) return;
    const req = this.agentId
      ? this.api.updateAgentBehavior(this.projectId, this.agentId, this.payload, this.selectedWorkType)
      : this.api.updateProjectBehavior(this.projectId, this.payload, this.selectedWorkType);

    req.subscribe({
      next: () => {
        this.statusMsg = '配置已成功保存！';
        this.loadConfig();
      },
      error: (err) => {
        this.statusMsg = '保存失败，请重试';
        console.error(err);
      },
    });
  }

  resetConfig(): void {
    if (!this.projectId) return;
    const req = this.agentId
      ? this.api.resetAgentBehavior(this.projectId, this.agentId, this.selectedWorkType)
      : this.api.resetProjectBehavior(this.projectId, this.selectedWorkType);

    req.subscribe({
      next: () => {
        this.statusMsg = '已重置为默认配置';
        this.loadConfig();
      },
      error: (err) => console.error(err),
    });
  }

  loadPreview(): void {
    if (!this.projectId) return;
    this.api.previewAgentBehavior(this.projectId, {
      work_type: this.selectedWorkType,
      agent_id: this.agentId,
      payload: this.payload,
    }).subscribe({
      next: (res) => {
        this.previewResult = res;
      },
      error: (err) => console.error('Preview error', err),
    });
  }
}