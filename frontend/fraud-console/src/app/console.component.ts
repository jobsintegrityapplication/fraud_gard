import { CommonModule } from '@angular/common';
import { HttpClient, HttpClientModule } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, NavigationEnd, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { filter } from 'rxjs';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';
import { AuthService } from './auth.service';

type Row = Record<string, any>;
@Component({
  selector: 'app-console', standalone: true,
  imports: [CommonModule, FormsModule, HttpClientModule, RouterLink, RouterLinkActive, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatProgressSpinnerModule, MatSidenavModule, MatToolbarModule],
  templateUrl: './console.component.html', styleUrl: './console.component.scss'
})
export class ConsoleComponent implements OnInit {
  private http = inject(HttpClient); private router = inject(Router); private route = inject(ActivatedRoute); auth = inject(AuthService);
  page = 'dashboard'; busy = false; apiOnline = false; error = ''; message = ''; search = ''; actor = 'analyst';
  dashboard: Row | null = null; rows: Row[] = []; item: Row | null = null; scores: Row | null = null; alertScores: Row | null = null; graph: Row | null = null;
  deployment: Row | null = null; modelRows: Row[] = []; learning: Row[] = []; reports: Row[] = []; auditRows: Row[] = [];
  gateResults: Record<string, Row> = {};
  decisionReason = ''; canaryPercent = 10;
  readonly nav = [
    { title: 'OVERVIEW', links: [{ label: 'Dashboard', path: '/dashboard', icon: '▦' }] },
    { title: 'INVESTIGATE', links: [{ label: 'Alerts', path: '/alerts', icon: '◉' }, { label: 'Cases', path: '/cases', icon: '▤' }, { label: 'Patterns', path: '/patterns', icon: '⌘' }] },
    { title: 'INTELLIGENCE', links: [{ label: 'Models', path: '/models', icon: '◈' }, { label: 'Learning', path: '/learning', icon: '↗' }, { label: 'Reports', path: '/reports', icon: '▧' }] },
    { title: 'GOVERNANCE', links: [{ label: 'Audit log', path: '/admin/audit', icon: '≡' }] }
  ];
  get id(): string { return this.route.snapshot.paramMap.get('id') || this.router.url.split('/').pop() || ''; }
  get title(): string { return ({ dashboard: 'Risk overview', alerts: 'Alert queue', cases: 'Fraud cases', 'case-detail': 'Case investigation', patterns: 'Pattern intelligence', 'pattern-detail': 'Pattern details', models: 'Model operations', learning: 'Continuous learning', reports: 'Investigation reports', audit: 'Audit log' } as Record<string, string>)[this.page] || 'Risk overview'; }

  async ngOnInit(): Promise<void> { this.syncPage(); await this.auth.initialize(); this.actor = this.auth.session?.actor_id || 'analyst'; if (this.auth.error) this.error = this.auth.error; this.load(); this.router.events.pipe(filter(e => e instanceof NavigationEnd)).subscribe(() => { this.syncPage(); this.load(); }); }
  private syncPage(): void {
    const p = this.router.url.split('?')[0].split('/').filter(Boolean);
    this.page = p[0] === 'cases' && p.length > 1 ? 'case-detail' : p[0] === 'patterns' && p.length > 1 ? 'pattern-detail' : ({ 'admin': 'audit' } as any)[p[0]] || p[0] || 'dashboard';
    if (p[0] === 'admin') this.page = 'audit';
  }
  load(): void {
    this.busy = true; this.error = ''; this.message = ''; this.rows = []; this.item = null;
    const done = () => this.busy = false;
    const get = (path: string, cb: (v: any) => void) => this.http.get<any>(`/api/v1${path}`).subscribe({ next: v => { this.apiOnline = true; cb(v); }, error: e => { this.apiOnline = false; this.error = e.error?.detail || e.message || 'Could not load data from the API'; done(); } });
    switch (this.page) {
      case 'dashboard': get('/dashboard', v => { this.dashboard = v; done(); }); break;
      case 'alerts': get('/alerts', v => { this.rows = Array.isArray(v) ? v : v.alerts || []; done(); }); break;
      case 'cases': get('/cases', v => { this.rows = v; done(); }); break;
      case 'case-detail':
        get(`/cases/${encodeURIComponent(this.id)}`, v => { this.item = v; done(); if (v.transaction_id) {
          this.http.post<any>('/api/v1/detection/score', { transaction_id: v.transaction_id }).subscribe({ next: x => this.scores = x, error: () => {} });
          this.http.get<any>(`/api/v1/graph/${encodeURIComponent(v.transaction_id)}`).subscribe({ next: x => this.graph = x, error: () => {} });
          this.http.get<any[]>('/api/v1/alerts').subscribe({ next: alerts => this.alertScores = alerts.find(a => a.transaction_id === v.transaction_id) || null, error: () => {} });
        } }); break;
      case 'patterns': get('/patterns', v => { this.rows = Array.isArray(v) ? v : v.patterns || []; done(); }); break;
      case 'pattern-detail': get(`/patterns/${encodeURIComponent(this.id)}`, v => { this.item = v; done(); }); break;
      case 'models':
        get('/models', v => { this.modelRows = v.models || []; done(); });
        this.http.get<any>('/api/v1/models/deployment').subscribe({ next: v => this.deployment = v, error: () => {} }); break;
      case 'learning': get('/learning/queue', v => { this.learning = v.items || []; done(); }); break;
      case 'reports': get('/reports', v => { this.reports = v.reports || []; done(); }); break;
      case 'audit': get('/audit', v => { this.auditRows = v.events || []; done(); }); break;
      default: get('/dashboard', v => { this.dashboard = v; this.page = 'dashboard'; done(); });
    }
  }
  filtered(rows: Row[]): Row[] { const q = this.search.toLowerCase().trim(); return !q ? rows : rows.filter(r => JSON.stringify(r).toLowerCase().includes(q)); }
  value(v: any, digits = 3): string { if (v === null || v === undefined || v === '') return '—'; return typeof v === 'number' ? (Number.isInteger(v) ? `${v}` : v.toFixed(digits)) : `${v}`; }
  open(path: string): void { this.router.navigateByUrl(path); }
  startCase(alert: Row): void { this.http.post<any>(`/api/v1/cases/from-alert/${alert['id']}`, {}).subscribe({ next: c => this.open(`/cases/${c['id'] || c['case_id']}`), error: e => this.error = e.error?.detail || 'Could not create case' }); }
  decide(decision: string): void {
    if (!this.item) return;
    const reason = this.decisionReason.trim(); if (!reason) { this.error = 'Add a decision reason before submitting.'; return; }
    this.http.post<any>(`/api/v1/cases/${this.item['id'] || this.item['case_id']}/decision`, { decision, reason, reviewer: this.actor, timestamp: new Date().toISOString(), evidence_version: this.item['evidence_version'] }).subscribe({ next: v => { this.item = v; this.message = 'Decision recorded and audit trail updated.'; this.error = ''; }, error: e => this.error = e.error?.detail || 'Could not save decision' });
  }
  transitionPattern(state: string): void { this.http.post<any>(`/api/v1/patterns/${this.id}/${state === 'UNDER_REVIEW' ? 'review' : state === 'ACTIVE' ? 'activate' : state === 'DEPRECATED' ? 'deprecate' : 'review'}`, state === 'UNDER_REVIEW' ? { notes: 'Reviewed in console' } : {}).subscribe({ next: v => { this.item = v; this.message = `Pattern moved to ${state}.`; }, error: e => this.error = e.error?.detail || 'Pattern transition failed' }); }
  deploy(modelId: string, action: string): void {
    const path = action === 'shadow' ? '/deployment/shadow' : action === 'canary' ? '/deployment/canary' : '/deployment/production';
    const body = action === 'canary' ? { model_id: modelId, percent: this.canaryPercent } : { model_id: modelId };
    this.http.post<any>(`/api/v1/models${path}`, body).subscribe({ next: () => { this.message = `Deployment updated.`; this.load(); }, error: e => this.error = e.error?.detail || 'Deployment action failed' });
  }
  evaluateGates(model: Row): void {
    this.http.post<Row>('/api/v1/models/gates/evaluate', { metrics: model['metrics'] || {}, record: model }).subscribe({ next: result => this.gateResults[model['model_id']] = result, error: e => this.error = e.error?.detail || 'Gate evaluation failed' });
  }
  rollback(): void { this.http.post<any>('/api/v1/models/deployment/rollback', {}).subscribe({ next: () => { this.message = 'Rolled back to the last known good model.'; this.load(); }, error: e => this.error = e.error?.detail || 'Rollback failed' }); }
  startLearning(): void { this.http.post<any>('/api/v1/learning/jobs', {}).subscribe({ next: j => { this.message = `Learning job ${j.job_id || ''} started.`; this.load(); }, error: e => this.error = e.error?.detail || 'Could not start learning job' }); }
  generateReport(): void {
    if (!this.item) return;
    const state = { case: this.item, transaction_id: this.item['transaction_id'], case_id: this.item['id'] || this.item['case_id'], detection_signals: this.scores || {}, graph_evidence: this.graph || {}, retrieved_history: { findings: [] }, evidence_items: [] };
    this.http.post<any>('/api/v1/reports/generate', { state }).subscribe({ next: r => { this.message = `Report ${r.report_version} generated.`; this.openReport(r.report_version); }, error: e => this.error = e.error?.detail || 'Could not generate report' });
  }
  openReport(version: string): void { this.http.get(`/api/v1/reports/${encodeURIComponent(version)}/pdf`, { responseType: 'blob' }).subscribe({ next: file => window.open(URL.createObjectURL(file), '_blank', 'noopener'), error: e => this.error = e.error?.detail || 'Could not open report' }); }
  status(v: any): string { return `${v || 'UNKNOWN'}`.replaceAll('_', ' '); }
  asRows(obj: any): Row[] { return obj && typeof obj === 'object' ? Object.entries(obj).map(([key, value]) => ({ key, value })) : []; }
}
