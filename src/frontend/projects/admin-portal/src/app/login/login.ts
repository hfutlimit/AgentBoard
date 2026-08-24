import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { ApiService } from '../api.service';

@Component({
  selector: 'app-login',
  imports: [FormsModule, CommonModule],
  templateUrl: './login.html',
  styleUrl: './login.css',
})
export class Login {
  username = signal('');
  password = signal('');
  error = signal('');
  loading = signal(false);
  private api = inject(ApiService);
  private router = inject(Router);

  submit() {
    this.error.set('');
    const u = this.username().trim();
    const p = this.password();
    if (!u || !p) {
      this.error.set('请输入用户名和密码');
      return;
    }
    this.loading.set(true);
    this.api.login(u, p).subscribe({
      next: (res: any) => {
        localStorage.setItem('admin_portal_token', res.token);
        this.loading.set(false);
        this.router.navigateByUrl('/dashboard');
      },
      error: (err) => {
        this.loading.set(false);
        const detail = err?.error?.detail;
        this.error.set(typeof detail === 'string' ? detail : '登录失败，请检查凭据');
      },
    });
  }
}
