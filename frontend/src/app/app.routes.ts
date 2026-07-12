import { Component } from '@angular/core';
import { Routes } from '@angular/router';

@Component({ selector: 'app-route-anchor', template: '' })
class RouteAnchor {}

export const routes: Routes = [
  { path: '', component: RouteAnchor, pathMatch: 'full' },
  { path: 'projects', component: RouteAnchor },
  { path: 'project/:id', component: RouteAnchor },
  { path: 'epic/:id', component: RouteAnchor },
  { path: 'story/:id', component: RouteAnchor },
  { path: 'task/:id', component: RouteAnchor },
  { path: 'sprint/:id', component: RouteAnchor },
  { path: '**', component: RouteAnchor },
];
