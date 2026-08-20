import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output, ViewEncapsulation, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ApiKeyInfo, Project, UserProfile } from '../models';

export type ProfileEditField = 'display_name' | 'email' | 'avatar_url';
type AccountSettingsSection = 'profile' | 'security' | 'projects' | 'api-keys';

@Component({
  selector: 'app-account-settings',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './account-settings.html',
  styleUrl: './account-settings.css',
  encapsulation: ViewEncapsulation.None,
})
export class AccountSettingsComponent {
  @Input() profile: UserProfile | null = null;
  @Input() projects: Project[] = [];
  @Input() apiKeys: ApiKeyInfo[] = [];

  @Output() editProfile = new EventEmitter<ProfileEditField>();
  @Output() editPassword = new EventEmitter<void>();
  @Output() newProject = new EventEmitter<void>();
  @Output() newApiKey = new EventEmitter<void>();
  @Output() revokeApiKey = new EventEmitter<number>();

  readonly activeSection = signal<AccountSettingsSection>('profile');

  selectSection(section: AccountSettingsSection): void {
    this.activeSection.set(section);
    document.getElementById(`account-settings-${section}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}
