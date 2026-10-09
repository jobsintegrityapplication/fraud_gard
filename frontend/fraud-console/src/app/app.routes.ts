import { Routes } from '@angular/router';
import { ConsoleComponent } from './console.component';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  { path: '**', component: ConsoleComponent }
];
