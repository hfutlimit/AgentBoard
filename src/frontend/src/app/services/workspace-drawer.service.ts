import { Injectable, computed, signal } from '@angular/core';
import type { WorkspaceEntityTabKind } from './workspace-tabs.service';

/**
 * WorkspaceDrawerService — 工作台右侧 Drawer 状态（Story #435）
 *
 * 动机：Task 是最底层的执行单元，数量最多。让每个 Task 都占一个一级 Tab
 * 会让 Tab 条迅速溢出，而且 Task 通常是「瞄一眼」而不是「长期驻留」。
 * 所以 Task 默认在 Drawer 里打开，需要长期驻留时由用户显式提升为 Tab。
 *
 * 职责边界：本服务只管 Drawer 的开关与宽度，不做数据加载；
 * 数据仍走 host.openWorkspaceEntity() 的既有链路，避免多一条加载路径。
 */

export interface WorkspaceDrawerState {
  kind: WorkspaceEntityTabKind;
  entityId: number;
  title?: string;
}

const WIDTH_KEY = 'agentboard_ws_drawer_width';
const TASK_MODE_KEY = 'agentboard_ws_task_in_drawer';
/** 抽屉默认宽度 = 视口 60%（产品决策：详情要看得清，而不是「瞄一眼」） */
const DEFAULT_WIDTH_RATIO = 0.6;
/** 抽屉最小可拖动宽度；同时也是 default 的下限，避免窄屏 60% 反而比 360 还小 */
const MIN_WIDTH = 600;
/** 默认上限：留 120px 给左侧导航 / 顶栏呼吸空间 */
const MIN_AVAILABLE_WIDTH = 120;

@Injectable({ providedIn: 'root' })
export class WorkspaceDrawerService {
  private readonly _state = signal<WorkspaceDrawerState | null>(null);
  private readonly _width = signal<number>(this.readWidth());
  /** Task 点击默认行为：true = Drawer，false = 直接开 Tab（用户可在 Tab 条菜单切换） */
  private readonly _taskPrefersDrawer = signal<boolean>(this.readTaskMode());

  readonly state = this._state.asReadonly();
  readonly width = this._width.asReadonly();
  readonly taskPrefersDrawer = this._taskPrefersDrawer.asReadonly();
  readonly isOpen = computed(() => this._state() !== null);

  open(state: WorkspaceDrawerState): void {
    this._state.set(state);
  }

  close(): void {
    this._state.set(null);
  }

  /** 换一个实体：保持 Drawer 打开，只换内容（避免闪烁） */
  swap(state: WorkspaceDrawerState): void {
    this._state.set(state);
  }

  setWidth(px: number): void {
    const next = Math.max(MIN_WIDTH, Math.min(px, this.maxAllowedWidth()));
    this._width.set(next);
    this.writeNumber(WIDTH_KEY, next);
  }

  setTaskPrefersDrawer(value: boolean): void {
    this._taskPrefersDrawer.set(value);
    this.writeString(TASK_MODE_KEY, value ? '1' : '0');
  }

  toggleTaskMode(): void {
    this.setTaskPrefersDrawer(!this._taskPrefersDrawer());
  }

  private readWidth(): number {
    const raw = this.readString(WIDTH_KEY);
    const n = raw ? Number(raw) : NaN;
    if (Number.isFinite(n) && n >= MIN_WIDTH) return n;
    return this.defaultWidth();
  }

  /** 视口 60%，clamp 到 [MIN_WIDTH, maxAllowedWidth()] */
  private defaultWidth(): number {
    const viewport = typeof window === 'undefined' ? 1280 : window.innerWidth;
    const target = Math.round(viewport * DEFAULT_WIDTH_RATIO);
    return Math.max(MIN_WIDTH, Math.min(target, this.maxAllowedWidth()));
  }

  private maxAllowedWidth(): number {
    if (typeof window === 'undefined') return 1200;
    return Math.max(MIN_WIDTH, window.innerWidth - MIN_AVAILABLE_WIDTH);
  }

  private readTaskMode(): boolean {
    // 默认 true：Task 走 Drawer（Story #435 的产品决策）
    return this.readString(TASK_MODE_KEY) !== '0';
  }

  private readString(key: string): string | null {
    if (typeof localStorage === 'undefined') return null;
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  private writeString(key: string, value: string): void {
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(key, value);
    } catch {
      /* 隐私模式 / 配额满：静默降级 */
    }
  }

  private writeNumber(key: string, value: number): void {
    this.writeString(key, String(Math.round(value)));
  }
}
