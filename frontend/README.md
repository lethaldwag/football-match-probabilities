# Web app

React + TypeScript + Vite. See the [project README](../README.md) for setup.

```
npm install
npm run dev      # http://localhost:5173, proxies /api to http://localhost:8000
npm test         # component tests (synthetic data only)
npm run build    # type-check and production build
```

- `src/api/` typed API client and response types
- `src/hooks/useApi.ts` + `src/components/ui.tsx` (`Async`) are the single place where loading,
  error and empty states are handled
- `src/pages/` dashboard, match detail, model performance

Set `API_PROXY_TARGET` to point the dev server at a different backend, or `VITE_API_BASE_URL` to
call an API on another origin directly.
