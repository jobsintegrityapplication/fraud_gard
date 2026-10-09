import { Injectable } from '@angular/core';

interface OidcMetadata { authorization_endpoint: string; token_endpoint: string; }
interface ConsoleConfig { issuer?: string; clientId?: string; scope?: string; audience?: string; }
declare global { interface Window { __FRAUD_CONSOLE_CONFIG__?: ConsoleConfig; } }

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly tokenKey = 'fraud-console.access-token';
  private readonly stateKey = 'fraud-console.oidc-state';
  private readonly verifierKey = 'fraud-console.pkce-verifier';
  session: { authenticated: boolean; actor_id: string; roles: string[] } | null = null;
  error = '';
  get token(): string | null { return sessionStorage.getItem(this.tokenKey); }
  get configured(): boolean { return !!(window.__FRAUD_CONSOLE_CONFIG__?.issuer && window.__FRAUD_CONSOLE_CONFIG__?.clientId); }

  async initialize(): Promise<void> {
    try {
      const params = new URLSearchParams(location.search);
      const completedLogin = params.has('code');
      if (params.has('error')) throw new Error(params.get('error_description') || 'Identity provider sign-in failed');
      if (params.has('code')) await this.exchangeCode(params.get('code')!, params.get('state') || '');
      if (this.token) await this.loadSession(completedLogin);
    } catch (error: any) {
      this.error = error?.message || 'Sign-in could not be completed';
      sessionStorage.removeItem(this.tokenKey);
      this.session = null;
    }
  }

  async login(): Promise<void> {
    this.error = '';
    if (!this.configured) { this.error = 'Configure the OIDC issuer and client ID in the frontend runtime config first.'; return; }
    try {
      const metadata = await this.discovery();
      const state = this.randomText(32); const verifier = this.randomText(64);
      sessionStorage.setItem(this.stateKey, state); sessionStorage.setItem(this.verifierKey, verifier);
      const challenge = await this.sha256(verifier);
      const config = window.__FRAUD_CONSOLE_CONFIG__!;
      const url = new URL(metadata.authorization_endpoint);
      url.search = new URLSearchParams({
        response_type: 'code', client_id: config.clientId!, redirect_uri: location.origin + '/auth/callback',
        scope: config.scope || 'openid profile roles', state, code_challenge: challenge, code_challenge_method: 'S256',
        ...(config.audience ? { audience: config.audience } : {})
      }).toString();
      location.assign(url.toString());
    } catch (error: any) { this.error = error?.message || 'Could not contact the identity provider'; }
  }

  logout(): void { sessionStorage.removeItem(this.tokenKey); this.session = null; location.assign('/dashboard'); }

  private async exchangeCode(code: string, returnedState: string): Promise<void> {
    const expectedState = sessionStorage.getItem(this.stateKey); const verifier = sessionStorage.getItem(this.verifierKey);
    sessionStorage.removeItem(this.stateKey); sessionStorage.removeItem(this.verifierKey);
    if (!expectedState || returnedState !== expectedState || !verifier) throw new Error('OIDC state validation failed');
    const metadata = await this.discovery(); const config = window.__FRAUD_CONSOLE_CONFIG__!;
    const form = new URLSearchParams({ grant_type: 'authorization_code', client_id: config.clientId!, code,
      redirect_uri: location.origin + '/auth/callback', code_verifier: verifier });
    const response = await fetch(metadata.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: form });
    if (!response.ok) throw new Error('Identity provider rejected the authorization code');
    const tokens = await response.json();
    if (!tokens.access_token) throw new Error('Identity provider did not return an access token');
    sessionStorage.setItem(this.tokenKey, tokens.access_token);
    history.replaceState({}, document.title, '/dashboard');
  }

  private async loadSession(recordLogin: boolean): Promise<void> {
    const response = await fetch(recordLogin ? '/api/v1/auth/session' : '/api/v1/auth/me', { headers: { Authorization: `Bearer ${this.token}` } });
    if (!response.ok) throw new Error(response.status === 401 ? 'Your session expired. Sign in again.' : 'Your account could not access this platform.');
    this.session = await response.json();
  }

  private async discovery(): Promise<OidcMetadata> {
    const issuer = window.__FRAUD_CONSOLE_CONFIG__?.issuer?.replace(/\/$/, '');
    if (!issuer) throw new Error('OIDC issuer is not configured');
    const response = await fetch(`${issuer}/.well-known/openid-configuration`);
    if (!response.ok) throw new Error('Could not load OIDC discovery metadata');
    const metadata = await response.json();
    if (metadata.issuer?.replace(/\/$/, '') !== issuer) throw new Error('OIDC discovery issuer mismatch');
    if (!metadata.authorization_endpoint || !metadata.token_endpoint) throw new Error('OIDC endpoints are missing');
    return metadata;
  }

  private randomText(bytes: number): string {
    const data = crypto.getRandomValues(new Uint8Array(bytes));
    return btoa(String.fromCharCode(...data)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  private async sha256(value: string): Promise<string> {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
}
