# Image attribution

Every image in this folder was created for this project. There is no third party photography and no third party artwork here, so
there is no photographer to credit and no license to comply with.

| File | What it is | Author | License |
| --- | --- | --- | --- |
| `healthcare.svg`, `retail.svg`, `finance.svg`, `manufacturing.svg`, `public-sector.svg` | Illustrations for the industry pages and tiles | Hand drawn as SVG for this project | Same as the project |
| `../favicon.svg`, `../og-image.svg` and the PNG files generated from them | Mark, social preview and app icons | Drawn for this project (`scripts/build-images.mjs` renders the PNGs) | Same as the project |

## Photography

The plan for the site allows free licensed photography (Unsplash or Pexels). None is used yet: each photograph needs its
photographer, source URL and license checked by a person who has viewed it, and that record cannot be produced honestly
without doing so. When photographs are added they go in this folder as WebP files with explicit width and height, and each gets a
row here with the photographer, the source URL and the license.

## Product screenshots

The home page shows illustrations of the console drawn with the real design system and real values from the sample systems; they
are labelled as illustrations. Authentic screenshots are produced from the running console by `scripts/capture_screenshots.ts`
(Playwright) once the console pages exist.

## Fonts

Inter, self hosted through the `@fontsource-variable/inter` package (SIL Open Font License 1.1).
