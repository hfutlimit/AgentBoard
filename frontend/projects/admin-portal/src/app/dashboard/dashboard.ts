import { Component, inject, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { ApiService } from '../api.service';

@Component({
  selector: 'app-dashboard',
  imports: [CommonModule],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css',
})
export class Dashboard implements OnInit {
  user = signal<any>(null);
  private api = inject(ApiService);
  private router = inject(Router);

  ngOnInit() {
    this.api.me().subscribe({
      next: (u) => this.user.set(u),
      error: () => this.user.set({ username: 'Admin', is_admin: true }),
    });
  }

  logout() {
    localStorage.removeItem('admin_portal_token');
    this.router.navigateByUrl('/login');
  }
}
