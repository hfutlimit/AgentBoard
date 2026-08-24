import { CommonModule } from '@angular/common';
import { Component, ViewEncapsulation, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { WorkspaceHeadingComponent } from '../workspace-heading/workspace-heading';
import { ProjectDataService } from '../services/project-data.service';

/** Workspace Proposal detail adapter that reuses the root App's existing signals and actions. */
@Component({
  selector: 'app-proposal-detail-view',
  standalone: true,
  imports: [CommonModule, RouterLink, WorkspaceHeadingComponent],
  templateUrl: './proposal-detail-view.html',
  encapsulation: ViewEncapsulation.None,
})
export class ProposalDetailViewComponent {
  readonly host = inject(ProjectDataService).getWorkspaceHost<any>();

  readonly proposalItem = this.host.proposalItem;
  readonly ticketType = this.host.ticketType;
  readonly ticketEpicId = this.host.ticketEpicId;
  readonly ticketEpics = this.host.ticketEpics;
  readonly ticketStoryId = this.host.ticketStoryId;
  readonly ticketStories = this.host.ticketStories;
  readonly ticketGenerating = this.host.ticketGenerating;
  readonly proposalTicketRequests = this.host.proposalTicketRequests;
  readonly proposalTab = this.host.proposalTab;
  readonly proposalRounds = this.host.proposalRounds;
  readonly proposalSubmitting = this.host.proposalSubmitting;

  readonly projectName = (id: number): string => this.host.projectName(id);
  readonly proposalStatusLabel = (status: string): string => this.host.proposalStatusLabel(status);
  readonly advanceProposalStatus = (status: string): Promise<void> => this.host.advanceProposalStatus(status);
  readonly isAgentFailure = (proposal: any): boolean => this.host.isAgentFailure(proposal);
  readonly onTicketTypeChange = (type: string): void => this.host.onTicketTypeChange(type);
  readonly ticketTypeLabel = (type: string): string => this.host.ticketTypeLabel(type);
  readonly onTicketEpicChange = (event: Event): void => this.host.onTicketEpicChange(event);
  readonly onTicketStoryChange = (event: Event): void => this.host.onTicketStoryChange(event);
  readonly ticketFormValid = (): boolean => this.host.ticketFormValid();
  readonly startTicketGeneration = (proposal: any): Promise<void> => this.host.startTicketGeneration(proposal);
  readonly ticketRequestStatusLabel = (status: string): string => this.host.ticketRequestStatusLabel(status);
  readonly switchProposalTab = (tab: string): void => this.host.switchProposalTab(tab);
  readonly totalAnsweredCount = (proposal: any): number => this.host.totalAnsweredCount(proposal);
  readonly totalQuestionCount = (): number => this.host.totalQuestionCount();
  readonly currentOpenQuestions = (): any[] => this.host.currentOpenQuestions();
  readonly roundAnsweredCount = (round: any): number => this.host.roundAnsweredCount(round);
  readonly proposalPendingCount = (): number => this.host.proposalPendingCount();
  readonly proposalHasDraftToSubmit = (): boolean => this.host.proposalHasDraftToSubmit();
  readonly submitProposalRound = (): Promise<void> => this.host.submitProposalRound();
  readonly openRoundDetail = (round: any): void => this.host.openRoundDetail(round);
  readonly roundStatusLabel = (round: any): string => this.host.roundStatusLabel(round);
  readonly roundSummary = (round: any): string => this.host.roundSummary(round);
}
