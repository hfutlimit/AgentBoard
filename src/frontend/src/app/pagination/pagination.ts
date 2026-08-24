import { Component, EventEmitter, Input, Output } from '@angular/core';

@Component({
  selector: 'app-pagination',
  templateUrl: './pagination.html',
  styleUrl: './pagination.css',
})
export class PaginationComponent {
  @Input() total = 0;
  @Input() page = 1;
  @Input() pageSize = 20;
  @Input() label = '列表';
  @Output() pageChange = new EventEmitter<number>();

  get totalPages(): number {
    return Math.max(1, Math.ceil(this.total / this.pageSize));
  }

  get currentPage(): number {
    return Math.min(Math.max(1, this.page), this.totalPages);
  }

  get rangeStart(): number {
    return this.total === 0 ? 0 : (this.currentPage - 1) * this.pageSize + 1;
  }

  get rangeEnd(): number {
    return Math.min(this.currentPage * this.pageSize, this.total);
  }

  goTo(page: number): void {
    const nextPage = Math.min(Math.max(1, page), this.totalPages);
    if (nextPage !== this.currentPage) this.pageChange.emit(nextPage);
  }
}
