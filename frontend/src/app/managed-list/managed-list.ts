import { Component, EventEmitter, Input, Output, computed } from '@angular/core';
import { PaginationComponent } from '../pagination/pagination';

/**
 * ManagedListComponent — 阶段2（Epic 149 / Story 318）抽取的统一列表外壳。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §1.3）：
 * 消除单体 app.html 中 5 个项目内列表（epics / workitems / proposals / documents / members）
 * 重复的「加载骨架 / 错误重试 / 分页」三态壳，统一交互骨架。
 *
 * 采用内容投影（content projection）而非完全数据驱动：
 * - 各列表的筛选信号、条目模板差异较大（epics 进度条 / proposals 轮次 / documents 拖拽文件夹
 *   / members 表格），强行数据驱动会引入回归风险。
 * - 外壳统一三态 + 分页，条目与工具栏由各列表自行投影，既去重又保留各自交互细节。
 * - 后续阶段可在此外壳上叠加「列定义 / 筛选项」数据驱动能力而不破坏现有投影契约。
 *
 * 投影插槽：
 *   [ml-header]   列表头部（section-header：标题 + 计数 + 操作按钮），可选
 *   [ml-toolbar]  筛选 / 搜索工具栏，可选（始终渲染，加载态也保留）
 *   默认插槽      列表主体（条目渲染 + 自有空状态），仅在非 loading / 非 error 时渲染
 *
 * 状态优先级：loading > error > 主体。
 */
@Component({
  selector: 'app-managed-list',
  standalone: true,
  imports: [PaginationComponent],
  templateUrl: './managed-list.html',
  styleUrl: './managed-list.css',
})
export class ManagedListComponent {
  /** 是否处于加载态（显示骨架屏，隐藏主体）。 */
  @Input() loading = false;
  /** 加载失败信息；非空则显示错误态 + 重试按钮（隐藏主体）。 */
  @Input() error: string | null = null;
  /** 错误态前缀文案，如「Epic 列表加载失败」。 */
  @Input() errorPrefix = '列表加载失败';
  /** 骨架屏 aria-label / caption 文案。 */
  @Input() loadingLabel = '正在加载列表…';
  /** 骨架行数。 */
  @Input() skeletonRows = 5;
  /** 分页 total 条目数。 */
  @Input() total = 0;
  /** 当前页码（1-based）。 */
  @Input() page = 1;
  /** 每页条数；<=0 表示不显示分页。 */
  @Input() pageSize = 0;
  /** 分页 aria label。 */
  @Input() paginationLabel = '列表';
  /** 列表是否为空（仅用于控制空态下是否隐藏分页）。 */
  @Input() empty = false;
  /** 为 true 时，空列表隐藏分页（默认隐藏，与原 Backlog 行为一致）。 */
  @Input() hidePaginationOnEmpty = true;

  @Output() pageChange = new EventEmitter<number>();
  @Output() retry = new EventEmitter<void>();

  /** 骨架行数组（供 @for 渲染）。 */
  readonly skeletonArray = computed(() => {
    const n = Math.max(0, Math.min(20, this.skeletonRows | 0));
    return Array.from({ length: n }, (_, i) => i);
  });

  /** 是否显示分页：配置了 pageSize、非加载/错误态、且（非空 或 不因空隐藏）。 */
  get showPagination(): boolean {
    if (this.pageSize <= 0 || this.loading || this.error) return false;
    if (this.empty && this.hidePaginationOnEmpty) return false;
    return true;
  }
}
