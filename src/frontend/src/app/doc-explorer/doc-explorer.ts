import { Component, EventEmitter, Input, Output, ViewEncapsulation, signal } from '@angular/core';
import type { OnChanges, SimpleChanges } from '@angular/core';
import type { DocumentItem, DocumentFolder, DocumentType } from '../models';
import type { DocDragItem } from '../documents-tab/documents-tab';

/**
 * TreeNode — 扁平化后的可见树节点（VS Code 资源管理器风格）。
 * depth 决定缩进；folder 节点 id=null 表示项目虚拟根。
 */
export interface DocTreeNode {
  kind: 'folder' | 'doc';
  key: string;
  id: number | null;
  name: string;
  depth: number;
  folder?: DocumentFolder;
  doc?: DocumentItem;
  hasChildren: boolean;
  isExpanded: boolean;
  count: number;
  path?: string;
}

/**
 * DocExplorerComponent — VS Code 风格文档资源管理器（左侧常驻目录树）。
 *
 * 设计目标：解决"一堆文件看不清结构"——完整展示 文件夹/文档 层级，
 * 支持展开/折叠、标题快速搜索、拖拽归档，且任何文件夹都可"以 workspace
 * 方式打开"（设为树根，仅浏览该子树；可一键返回项目根）。
 *
 * 状态契约：业务状态（当前文件夹/workspace 根）由宿主 App 持有，
 * 本组件仅持有 UI 状态（展开集合/搜索词），与 DocumentsTabComponent 的
 * 无状态 emit 模式一致（F8）。
 *
 * 数据契约（@Input）：
 *   folders          当前项目全部文件夹（未过滤，结构真源）
 *   docs             当前项目全部文档（未过滤，结构真源）
 *   currentFolderId  当前浏览文件夹（右侧列表同步高亮）
 *   workspaceRootId  workspace 根文件夹（null = 项目根）
 *   projectName      项目名（虚拟根节点显示名）
 *   dragItem / dropTargetId  拖拽状态（复用宿主既有 DnD 契约）
 *
 * 事件契约（@Output）：
 *   openFolder(id|null)   点击文件夹 → 宿主 enterDocFolder
 *   openDoc(doc)          点击文档 → 宿主 openDocTab
 *   setWorkspaceRoot(id|null)  以文件夹为 workspace 打开 / 返回项目根
 *   renameFolder / deleteFolder / createDoc / createFolder  复用宿主入口
 *   dragStart / dragEnd / dropOver / dropLeave / drop  复用宿主 DnD
 */
@Component({
  selector: 'app-doc-explorer',
  standalone: true,
  templateUrl: './doc-explorer.html',
  styleUrl: './doc-explorer.css',
  encapsulation: ViewEncapsulation.None,
})
export class DocExplorerComponent implements OnChanges {
  @Input() folders: DocumentFolder[] = [];
  @Input() docs: DocumentItem[] = [];
  @Input() currentFolderId: number | null = null;
  @Input() workspaceRootId: number | null = null;
  @Input() projectName = '';
  @Input() dragItem: DocDragItem | null = null;
  @Input() dropTargetId: number | 'root' | null = null;

  @Output() openFolder = new EventEmitter<number | null>();
  @Output() openDoc = new EventEmitter<DocumentItem>();
  @Output() setWorkspaceRoot = new EventEmitter<number | null>();
  @Output() renameFolder = new EventEmitter<DocumentFolder>();
  @Output() deleteFolder = new EventEmitter<DocumentFolder>();
  @Output() createDoc = new EventEmitter<void>();
  @Output() createFolder = new EventEmitter<void>();
  @Output() dragStart = new EventEmitter<{ event: DragEvent; item: DocDragItem }>();
  @Output() dragEnd = new EventEmitter<void>();
  @Output() dropOver = new EventEmitter<{ event: DragEvent; target: number | 'root' }>();
  @Output() dropLeave = new EventEmitter<void>();
  @Output() drop = new EventEmitter<{ event: DragEvent; target: number | 'root' }>();

  /** 已展开文件夹 id 集合（UI 状态）。 */
  readonly expanded = signal<Set<number>>(new Set());
  /** 虚拟项目根是否展开（默认展开）。 */
  readonly rootExpanded = signal(true);
  /** 树内快速搜索词（标题/文件夹名）。 */
  readonly query = signal('');

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['currentFolderId'] || changes['folders'] || changes['workspaceRootId']) {
      this.revealFolder(this.currentFolderId);
    }
  }

  /** 展开某文件夹的全部祖先（含自身），保证选中节点在树中可见。 */
  private revealFolder(fid: number | null): void {
    if (fid === null) return;
    const byId = new Map(this.folders.map((f) => [f.id, f]));
    const next = new Set(this.expanded());
    let cur: number | null = fid;
    const seen = new Set<number>();
    while (cur !== null && !seen.has(cur)) {
      seen.add(cur);
      next.add(cur);
      cur = byId.get(cur)?.parent_id ?? null;
    }
    this.expanded.set(next);
  }

  /** 文档类型图标（与类型语义对应，便于扫读结构）。 */
  docIcon(t: DocumentType): string {
    return { memory: '🧠', plan: '📋', knowledge: '📖', design: '🎨' }[t] || '📄';
  }

  /** workspace 根显示名。 */
  rootName(): string {
    if (this.workspaceRootId === null) return this.projectName || '全部文档';
    return this.folders.find((f) => f.id === this.workspaceRootId)?.name || `文件夹 #${this.workspaceRootId}`;
  }

  /** 折叠全部（保留虚拟根展开）。 */
  collapseAll(): void {
    this.expanded.set(new Set());
  }

  /** 文件夹行点击：切换展开 + 同步右侧列表浏览。 */
  onFolderClick(n: DocTreeNode): void {
    if (n.id === null) {
      this.rootExpanded.set(true);
      this.openFolder.emit(null);
      return;
    }
    this.toggleExpand(n.id);
    this.openFolder.emit(n.id);
  }

  /** 箭头点击：仅切换展开，不改变浏览位置。 */
  onChevronClick(event: MouseEvent, n: DocTreeNode): void {
    event.stopPropagation();
    if (n.id === null) {
      this.rootExpanded.update((v) => !v);
      return;
    }
    this.toggleExpand(n.id);
  }

  private toggleExpand(fid: number): void {
    const next = new Set(this.expanded());
    if (next.has(fid)) next.delete(fid);
    else next.add(fid);
    this.expanded.set(next);
  }

  onFolderDragOver(event: DragEvent, fid: number | null): void {
    event.preventDefault();
    event.stopPropagation();
    this.dropOver.emit({ event, target: fid === null ? 'root' : fid });
  }

  onFolderDrop(event: DragEvent, fid: number | null): void {
    event.preventDefault();
    event.stopPropagation();
    this.drop.emit({ event, target: fid === null ? 'root' : fid });
  }

  /** 当前 workspace 子树的文件夹 id 集合（null = 全项目）。 */
  private subtreeIds(): Set<number> | null {
    if (this.workspaceRootId === null) return null;
    const children = new Map<number | null, number[]>();
    for (const f of this.folders) {
      const k = f.parent_id ?? null;
      const arr = children.get(k) ?? [];
      arr.push(f.id);
      children.set(k, arr);
    }
    const out = new Set<number>([this.workspaceRootId]);
    const stack = [this.workspaceRootId];
    while (stack.length) {
      const cur = stack.pop()!;
      for (const c of children.get(cur) ?? []) {
        if (!out.has(c)) {
          out.add(c);
          stack.push(c);
        }
      }
    }
    return out;
  }

  /** 文件夹路径（搜索结果副标题）。 */
  private pathFor(fid: number | null): string {
    const byId = new Map(this.folders.map((f) => [f.id, f]));
    const parts: string[] = [];
    let cur = fid;
    const seen = new Set<number>();
    while (cur !== null && !seen.has(cur)) {
      seen.add(cur);
      const f = byId.get(cur);
      if (!f) break;
      parts.unshift(f.name);
      cur = f.parent_id;
    }
    return parts.join(' / ');
  }

  /** 扁平化可见节点（每轮变更检测重算；数百文档规模开销可忽略）。 */
  nodes(): DocTreeNode[] {
    const q = this.query().trim().toLowerCase();
    if (q) return this.searchNodes(q);

    const childFolders = new Map<number | null, DocumentFolder[]>();
    for (const f of this.folders) {
      const k = f.parent_id ?? null;
      const arr = childFolders.get(k) ?? [];
      arr.push(f);
      childFolders.set(k, arr);
    }
    for (const arr of childFolders.values()) {
      arr.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'));
    }
    const docsByFolder = new Map<number | null, DocumentItem[]>();
    for (const d of this.docs) {
      const k = d.folder_id ?? null;
      const arr = docsByFolder.get(k) ?? [];
      arr.push(d);
      docsByFolder.set(k, arr);
    }
    for (const arr of docsByFolder.values()) {
      arr.sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN'));
    }
    const countFor = (fid: number | null): number => (docsByFolder.get(fid) ?? []).length;
    const hasChildren = (fid: number | null): boolean =>
      (childFolders.get(fid)?.length ?? 0) > 0 || (docsByFolder.get(fid)?.length ?? 0) > 0;

    const out: DocTreeNode[] = [];
    const seen = new Set<number>();
    const walk = (fid: number | null, depth: number): void => {
      if (fid !== null) {
        if (seen.has(fid)) return;
        seen.add(fid);
      }
      for (const f of childFolders.get(fid) ?? []) {
        const exp = this.expanded().has(f.id);
        out.push({
          kind: 'folder', key: 'f' + f.id, id: f.id, name: f.name, depth,
          folder: f, hasChildren: hasChildren(f.id), isExpanded: exp, count: countFor(f.id),
        });
        if (exp) walk(f.id, depth + 1);
      }
      for (const d of docsByFolder.get(fid) ?? []) {
        out.push({
          kind: 'doc', key: 'd' + d.id, id: d.id, name: d.title, depth,
          doc: d, hasChildren: false, isExpanded: false, count: 0,
        });
      }
    };

    const rootFolder =
      this.workspaceRootId !== null
        ? this.folders.find((f) => f.id === this.workspaceRootId) ?? null
        : null;
    if (rootFolder) {
      out.push({
        kind: 'folder', key: 'f' + rootFolder.id, id: rootFolder.id, name: rootFolder.name,
        depth: 0, folder: rootFolder, hasChildren: hasChildren(rootFolder.id),
        isExpanded: true, count: countFor(rootFolder.id),
      });
      walk(rootFolder.id, 1);
    } else {
      const exp = this.rootExpanded();
      out.push({
        kind: 'folder', key: 'root', id: null, name: this.projectName || '全部文档',
        depth: 0, hasChildren: hasChildren(null), isExpanded: exp, count: countFor(null),
      });
      if (exp) walk(null, 1);
    }
    return out;
  }

  /** 搜索模式：扁平匹配结果（文件夹优先，文档带路径副标题），限 workspace 子树。 */
  private searchNodes(q: string): DocTreeNode[] {
    const scope = this.subtreeIds();
    const out: DocTreeNode[] = [];
    for (const f of this.folders) {
      if (scope && !scope.has(f.id)) continue;
      if (f.name.toLowerCase().includes(q)) {
        out.push({
          kind: 'folder', key: 'f' + f.id, id: f.id, name: f.name, depth: 0,
          folder: f, hasChildren: false, isExpanded: false, count: 0,
        });
      }
    }
    for (const d of this.docs) {
      if (scope && (d.folder_id === null || !scope.has(d.folder_id))) continue;
      if (d.title.toLowerCase().includes(q)) {
        out.push({
          kind: 'doc', key: 'd' + d.id, id: d.id, name: d.title, depth: 0,
          doc: d, hasChildren: false, isExpanded: false, count: 0, path: this.pathFor(d.folder_id),
        });
      }
    }
    return out;
  }
}