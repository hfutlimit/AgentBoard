import { Component, EventEmitter, inject, Input, OnChanges, OnInit, Output, signal, SimpleChanges, ViewEncapsulation } from '@angular/core';
import { DatePipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ManagedListComponent } from '../managed-list/managed-list';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import { ApiService } from '../api.service';
import type { DocumentItem, DocumentFolder, DocumentType, DocumentStatus, Epic, Project } from '../models';

/**
 * DocBreadcrumbItem — 面包屑项（id null = 根目录）。
 */
export interface DocBreadcrumbItem {
  id: number | null;
  name: string;
}

/**
 * DocAuthorOption — 作者过滤选项。
 */
export interface DocAuthorOption {
  user_id: number;
  username: string;
}

/**
 * DocDragItem — 拖拽中的项（document 或 folder）。
 */
export interface DocDragItem {
  kind: 'document' | 'folder';
  id: number;
}

type DocSortBy = 'updated' | 'created' | 'title';
type DocListViewMode = 'tile' | 'list';

/**
 * DocumentsTabComponent — 阶段3（Epic 149 / Story 319）从单体 app.html @switch 拆出的
 * 项目「文档」视图独立组件（5/8）。
 *
 * 设计目标（见 docs/design-prototypes/layout-rebuild/codex/MIGRATION.md §2）：
 * 将项目工作区 documents tab 从单体模板中拆出（151 行，8 视图中最复杂），套用原型 v7
 * 的卡片视觉骨架，同时保留原有业务逻辑（面包屑导航 / 拖拽归档 / 文件夹 tile / 文档
 * list+tile 双视图 / 类型+状态+作者+Epic+排序+搜索 6 维过滤 / 新建文档+文件夹入口）。
 *
 * 与 ManagedListComponent 关系：
 *   documents tab 在阶段2 已套用 ManagedListComponent 外壳，本次将「外壳 + 主体」整体抽出。
 *
 * 数据契约（@Input）：
 *   docs              已过滤+排序后的文档列表（来自 App.applyDocSort(App.projectDocVisible())）
 *   childFolders      当前目录子文件夹（来自 App.docChildFolders()）
 *   breadcrumb        面包屑路径（来自 App.docBreadcrumb()）
 *   authorOptions     作者过滤选项（来自 App.docAuthorOptions()）
 *   epics             Epic 列表（来自 App.epics()，用于 Epic 过滤选项）
 *   filterType / filterStatus / searchQuery / filterAuthor / filterEpic / sortBy / listViewMode
 *                     父组件 signals 当前值（无状态子组件 emit 模式，F8）
 *   docTypes / docStatuses  枚举数组
 *   loading / error   加载状态
 *   dragItem          当前拖拽项（来自 App.docDrag()）
 *   dropTargetId      当前 drop 目标 id（来自 App.docDropId()）
 *   epicTitleFor / docScopePathFor / docFolderCountFor / docCommentCountFor
 *                     查询函数（箭头属性 Input，F7 模式，this 绑定到 App）
 *
 * 事件契约（@Output）：
 *   filterTypeChange / filterStatusChange / searchQueryChange / filterAuthorChange /
 *   filterEpicChange / sortByChange / listViewModeChange  —— 父组件 signal.set($event)
 *   enterFolder(id)         —— App.enterDocFolder(id)
 *   createDoc()             —— App.openDocModal('create')
 *   createFolder()          —— App.openDocFolderModal('create')
 *   renameFolder(folder)    —— App.openDocFolderModal('rename', folder)
 *   deleteFolder(folder)    —— App.deleteDocFolder(folder)
 *   openDoc(doc)            —— App.openDocTab(doc)
 *   dragStart(item)         —— App.onDocDragStart(event, item)（event 由父组件从 $event 获取）
 *   dragEnd()               —— App.onDocDragEnd()
 *   dropOver(target)        —— App.onDocDropOver(event, target)
 *   dropLeave()             —— App.onDocDropLeave()
 *   drop(target)            —— App.onDocDrop(event, target)
 *
 * 视觉：基础规则复用全局 .doc-list / .doc-folder-tile / .doc-breadcrumb / .doc-toolbar
 * （ViewEncapsulation.None），本组件 css 仅补 v7 增强。
 */
@Component({
  selector: 'app-documents-tab',
  standalone: true,
  imports: [ManagedListComponent, DatePipe, RouterLink, WorkspaceHeadingComponent],
  templateUrl: './documents-tab.html',
  styleUrl: './documents-tab.css',
  encapsulation: ViewEncapsulation.None,
})
export class DocumentsTabComponent implements OnInit, OnChanges {
  @Input() scope: 'project' | 'global' = 'project';
  private readonly route = inject(ActivatedRoute);
  ngOnInit(): void {
    // 优先用路由 data.scope 覆盖（#1428 修复：/documents 全局路由传 'global'）
    const routeScope = this.route.snapshot.data['scope'] as 'project' | 'global' | undefined;
    if (routeScope) this.scope = routeScope;
  }
  /** 全局视图下需要项目列表，用于文档列表显示"归属项目"列 */
  @Input() projects: Project[] = [];

  @Input({ required: true }) docs: DocumentItem[] = [];
  @Input() childFolders: DocumentFolder[] = [];
  @Input() breadcrumb: DocBreadcrumbItem[] = [];
  @Input() authorOptions: DocAuthorOption[] = [];
  @Input() epics: Epic[] = [];
  @Input() filterType: DocumentType | '' = '';
  @Input() filterStatus: DocumentStatus | '' = '';
  @Input() searchQuery = '';
  @Input() filterAuthor: number | '' = '';
  @Input() filterEpic: number | '' = '';
  @Input() sortBy: DocSortBy = 'updated';
  @Input() listViewMode: DocListViewMode = 'tile';
  @Input() docTypes: DocumentType[] = [];
  @Input() docStatuses: DocumentStatus[] = [];
  @Input() loading = false;
  @Input() error = '';
  @Input() dragItem: DocDragItem | null = null;
  @Input() dropTargetId: number | 'root' | null = null;

  // 查询函数 Input（箭头属性，F7 模式，this 绑定到 App 实例）
  @Input() epicTitleFor: (eid: number | null) => string = () => '';
  @Input() docScopePathFor: (d: DocumentItem) => string = () => '';
  @Input() docFolderCountFor: (fid: number) => number = () => 0;
  @Input() docCommentCountFor: (docId: number) => number = () => 0;

  @Output() filterTypeChange = new EventEmitter<DocumentType | ''>();
  @Output() filterStatusChange = new EventEmitter<DocumentStatus | ''>();
  @Output() searchQueryChange = new EventEmitter<string>();
  @Output() filterAuthorChange = new EventEmitter<number | ''>();
  @Output() filterEpicChange = new EventEmitter<number | ''>();
  @Output() sortByChange = new EventEmitter<DocSortBy>();
  @Output() listViewModeChange = new EventEmitter<DocListViewMode>();
  @Output() enterFolder = new EventEmitter<number | null>();
  @Output() createDoc = new EventEmitter<void>();
  @Output() createFolder = new EventEmitter<void>();
  @Output() renameFolder = new EventEmitter<DocumentFolder>();
  @Output() deleteFolder = new EventEmitter<DocumentFolder>();
  @Output() openDoc = new EventEmitter<DocumentItem>();
  @Output() dragStart = new EventEmitter<{ event: DragEvent; item: DocDragItem }>();
  @Output() dragEnd = new EventEmitter<void>();
  @Output() dropOver = new EventEmitter<{ event: DragEvent; target: number | 'root' }>();
  @Output() dropLeave = new EventEmitter<void>();
  @Output() drop = new EventEmitter<{ event: DragEvent; target: number | 'root' }>();
  @Output() retry = new EventEmitter<void>();

  /** 文档类型文案（与 App.docTypeLabel 一致，纯函数复制）。 */
  docTypeLabel(t: DocumentType): string {
    return { memory: '记忆', plan: '计划', knowledge: '知识', design: '设计' }[t] || t;
  }

  /** 文档状态文案（与 App.docStatusLabel 一致，纯函数复制）。 */
  docStatusLabel(s: DocumentStatus): string {
    return { draft: '草稿', in_review: '评审中', approved: '已批准', cancelled: '已取消' }[s] || s;
  }

  /** 文档摘要（与 App.docSummary 一致，纯函数，仅访问 d.content）。 */
  docSummary(d: DocumentItem): string {
    const text = (d.content || '').replace(/```[\s\S]*?```/g, ' ').trim();
    const first = text.split(/\r?\n/).find((line) => line.trim().length > 0) || '';
    const cleaned = first.replace(/^#+\s*/, '').replace(/[*_`>]/g, '').trim();
    return cleaned.length > 80 ? cleaned.slice(0, 80) + '…' : cleaned;
  }

  /** 相对时间（与 App.timeAgo 一致，纯函数复制）。 */
  timeAgo(dateStr: string): string {
    if (!dateStr) return '';
    const now = Date.now();
    const date = new Date(dateStr).getTime();
    const diff = Math.floor((now - date) / 1000);
    if (diff < 60) return `${diff}s前`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h前`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d前`;
    return new Date(dateStr).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  }

  // ─── 全局模式（scope === 'global'）独立数据流 ───
  private readonly api = inject(ApiService);
  readonly globalDocs = signal<DocumentItem[]>([]);
  readonly globalLoading = signal(false);
  readonly globalError = signal('');

  /** 全局模式下文档来源：独立拉取（不依赖父组件 @Input） */
  get effectiveDocs(): DocumentItem[] {
    return this.scope === 'global' ? this.globalDocs() : this.docs;
  }
  get effectiveLoading(): boolean {
    return this.scope === 'global' ? this.globalLoading() : this.loading;
  }
  get effectiveError(): string {
    return this.scope === 'global' ? this.globalError() : this.error;
  }
  /** 全局模式下显示空目录（不显示文件夹树） */
  get showFolderTree(): boolean {
    return this.scope === 'project';
  }
  /** 全局模式下隐藏项目级筛选（author / epic） */
  get showProjectFilters(): boolean {
    return this.scope === 'project';
  }
  /** 全局模式下不显示面包屑（无项目上下文） */
  get showBreadcrumb(): boolean {
    return this.scope === 'project';
  }
  /** 全局模式下不显示新建按钮（创建文档需在项目内） */
  get showCreateButtons(): boolean {
    return this.scope === 'project';
  }
  /** 全局模式下不显示拖拽 */
  get enableDrag(): boolean {
    return this.scope === 'project';
  }
  /** 文档归属项目名（仅全局模式需要查 projects 输入） */
  projectNameFor(d: DocumentItem): string {
    const p = this.projects.find((x) => x.id === d.project_id);
    return p ? p.name : `#${d.project_id}`;
  }

  async ngOnChanges(changes: SimpleChanges): Promise<void> {
    // scope 切到 global 或 docs/projects 输入变化时，重新拉全局文档
    if (this.scope !== 'global') return;
    if (!changes['scope'] && !changes['projects']) return;
    this.globalLoading.set(true);
    this.globalError.set('');
    try {
      // 不传 project_id = 查所有项目文档（api.listDocuments 已支持）
      const rows = await firstValueFrom(this.api.listDocuments({ sort: 'updated' }));
      this.globalDocs.set(Array.isArray(rows) ? rows : []);
    } catch (e: any) {
      this.globalError.set(e?.error?.detail || e?.message || '全局文档加载失败');
      this.globalDocs.set([]);
    } finally {
      this.globalLoading.set(false);
    }
  }
}
