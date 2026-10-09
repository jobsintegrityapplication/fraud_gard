import { HttpInterceptorFn } from '@angular/common/http';

export const oidcBearerInterceptor: HttpInterceptorFn = (request, next) => {
  const token = sessionStorage.getItem('fraud-console.access-token');
  if (!token || !request.url.startsWith('/api/')) return next(request);
  return next(request.clone({ setHeaders: { Authorization: `Bearer ${token}` } }));
};
