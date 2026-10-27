# LegacyLift frontend

The public site and the console. React, TypeScript, Vite, Tailwind CSS, Radix UI, TanStack Query and Table, ECharts. How it is built and why is in
[docs/architecture.md](../docs/architecture.md) (Frontend foundation and public site, and The console).

## Run it

```
cd frontend
npm ci
npm run dev          # http://127.0.0.1:5173, forwards /api to http://127.0.0.1:8000
```

The pages that show data (metrics, plans, the calculator, the forms, sign in) need the API: start it from `backend/` with
`uv run uvicorn app.main:app --port 8000`, and seed the demo users with `python -m app.seed` (and the demo history with
`make seed-billing` from the repository root). `VITE_API_TARGET` points the dev server at another API.

## Check it

| Command | What it does |
| --- | --- |
| `npm run lint` | ESLint, and a check that no file contains the long dash character |
| `npm run typecheck` | TypeScript, strict |
| `npm test` | Vitest component and unit tests: the wizard steps, the chart data transforms and builders, the shell, the formats |
| `npm run build` | renders the project docs, type checks, builds into `dist/` |
| `npm run e2e` | Playwright against the built site (`npm run preview` or `E2E_BASE_URL`): pages, widths, layout shift, links, accessibility, interactions, and the console (see below) |
| `npm run lighthouse` | Lighthouse (desktop) on ten pages; fails below performance 90 or accessibility 95 |

The browser tests need Playwright's browser once: `npx playwright install chromium`.

### The console tests

`e2e/console.spec.ts` drives the console against the real API with the real pipeline: each role signs in, the pages each role may and may not open,
the migration wizard from the first step to the reconciliation report on both sample systems (and a rollback), both dashboards with the seeded
history, and the accessibility of every console page in the light and the dark theme. It needs the whole stack, so it skips itself without
`E2E_PASSWORD`:

```
cd backend
docker compose up -d db redis                       # from the repository root
uv run python -m app.seed                           # the three demo users (SEED_*_PASSWORD in the environment)
uv run python -m app.mapping.seed_targets           # the commerce and clinic target schemas
uv run python -m app.billing.cli history --reset    # 24 months of simulated history (make seed-billing)
JOBS_EXECUTION=eager COOKIE_SECURE=false RATE_LIMIT_ENABLED=false uv run uvicorn app.main:app --port 8000

cd ../frontend
npm run build
E2E_PASSWORD=<the password of the demo users> npm run e2e
```

`JOBS_EXECUTION=eager` runs the stages inside the request, so no worker is needed; with a worker (`make worker`) the same tests run on the
queue. Sign in once per role happens in `e2e/auth.setup.ts`, so the run stays under the sign in rate limit.

Without the API (no Docker, CI), run the stand in and point the tests at it:

```
E2E_PASSWORD=<any password> node e2e/mock-api.mjs 8001
VITE_API_TARGET=http://127.0.0.1:8001 npm run preview
E2E_PASSWORD=<same> E2E_MOCK_API_URL=http://127.0.0.1:8001 npm run e2e
```

For Lighthouse serve the build the way production does (compression and caching): `node scripts/serve-dist.mjs 4174 <api url>`,
then `node scripts/lighthouse.mjs`.

## Where things are

- `src/styles/tokens.css`: the design tokens, the one place for colors, radii, shadows and the type scale.
- `src/site/content/`: the text of the site as typed data (navigation, stages, industries, FAQ, security, articles).
- `src/components/ui/`: the primitives (Radix and shadcn style). `src/components/`: header, footer, forms, pricing, sections.
- `src/console/`: the console. `shell/` (layout, navigation by role, top bar, command palette, theme), `pages/` (one file per page), `wizard/` (the seven steps and their helpers), `charts/` (palette, option builders, transforms, the chart card, the dashboards' charts), `ui/` (tables, dialogs, states, toasts), `api.ts` (every call and its types).
- `scripts/`: `build-docs`, `build-images` (icons and the social image from SVG), `screens` and `mobile-crops` (screenshots for review),
  `overflow` (finds what makes a page wider than the window), `serve-dist` and `lighthouse`.
- `../scripts/capture_screenshots.ts`: authentic screenshots of the running application, as WebP.

## Rules

Use no long dash character anywhere (a comma, a colon, parentheses or a hyphen). State only what the project does and has measured:
the content test fails a certification claim or a figure on an industry that has no sample system. Every image is original or has its
source recorded in `public/images/ATTRIBUTION.md`.
