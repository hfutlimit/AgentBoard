import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-login',
  imports: [CommonModule, FormsModule],
  templateUrl: './login.html',
  styleUrl: './login.css',
})
export class LoginComponent {
  @Input() mode: 'login' | 'register' = 'login';
  @Input() submitting = false;
  @Output() modeChange = new EventEmitter<'login' | 'register'>();
  @Output() authenticate = new EventEmitter<{ username: string; password: string }>();

  readonly username = signal('');
  readonly password = signal('');

  submit(): void {
    const username = this.username().trim();
    const password = this.password();
    if (!username || !password || this.submitting) return;
    this.authenticate.emit({ username, password });
  }

  switchMode(): void {
    this.modeChange.emit(this.mode === 'login' ? 'register' : 'login');
  }
}
