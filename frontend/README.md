# LegacyLift frontend

The public site and, in the next phase, the console. React, TypeScript, Vite, Tailwind CSS, Radix UI. How it is built and why is in
[docs/architecture.md](../docs/architecture.md) (Frontend foundation and public site).

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
| `npm test` | Vitest component and unit tests |
| `npm run build` | renders the project docs, type checks, builds into `dist/` |
| `npm run e2e` | Playwright against the built site (`npm run preview` or `E2E_BASE_URL`): pages, widths, layout shift, links, accessibility, interactions |
| `npm run lighthouse` | Lighthouse (desktop) on ten pages; fails below performance 90 or accessibility 95 |

The browser tests need Playwright's browser once: `npx playwright install chromium`.

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
- `scripts/`: `build-docs`, `build-images` (icons and the social image from SVG), `screens` and `mobile-crops` (screenshots for review),
  `overflow` (finds what makes a page wider than the window), `serve-dist` and `lighthouse`.
- `../scripts/capture_screenshots.ts`: authentic screenshots of the running application, as WebP.

## Rules

Use no long dash character anywhere (a comma, a colon, parentheses or a hyphen). State only what the project does and has measured:
the content test fails a certification claim or a figure on an industry that has no sample system. Every image is original or has its
source recorded in `public/images/ATTRIBUTION.md`.
