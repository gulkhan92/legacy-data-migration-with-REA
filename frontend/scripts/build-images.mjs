// Renders the PNG icons and the social preview image from the SVG sources in public/. Run when the sources change: npm run images
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const pub = resolve(dirname(fileURLToPath(import.meta.url)), "../public");
const favicon = readFileSync(resolve(pub, "favicon.svg"));
const og = readFileSync(resolve(pub, "og-image.svg"));

const icons = [
  ["favicon-32.png", 32],
  ["apple-touch-icon.png", 180],
  ["icon-192.png", 192],
  ["icon-512.png", 512],
];

for (const [name, size] of icons) {
  const png = await sharp(favicon, { density: Math.max(72, size * 6) }).resize(size, size).png({ compressionLevel: 9 }).toBuffer();
  writeFileSync(resolve(pub, name), png);
  console.log(`${name} ${size}x${size} ${png.length} bytes`);
}
const ogPng = await sharp(og, { density: 96 }).resize(1200, 630).png({ compressionLevel: 9 }).toBuffer();
writeFileSync(resolve(pub, "og-image.png"), ogPng);
console.log(`og-image.png 1200x630 ${ogPng.length} bytes`);
