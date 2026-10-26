// A production like server for the built site, for measuring (Lighthouse) without nginx: gzip for text, long lived caching for hashed
// assets, no caching for the page shell, a fallback to index.html for client side routes, and a proxy for the API.
// Usage: node scripts/serve-dist.mjs [port] [apiTarget]      (run `npm run build` first)
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { extname, join, normalize, resolve } from "node:path";
import { createGzip } from "node:zlib";

const port = Number(process.argv[2] ?? 4174);
const api = new URL(process.argv[3] ?? "http://127.0.0.1:8001");
const root = resolve(import.meta.dirname, "../dist");
const types = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
  ".png": "image/png", ".woff2": "font/woff2", ".webmanifest": "application/manifest+json", ".xml": "application/xml", ".txt": "text/plain; charset=utf-8", ".ico": "image/x-icon",
};
const compressible = new Set([".html", ".js", ".css", ".json", ".svg", ".webmanifest", ".xml", ".txt"]);
// The security headers are read from nginx.conf, so what is tested here is exactly what ships.
const nginx = readFileSync(resolve(import.meta.dirname, "../nginx.conf"), "utf8");
const pageBlock = nginx.slice(nginx.indexOf("location / {"));
const securityHeaders = Object.fromEntries([...pageBlock.matchAll(/add_header ([A-Za-z-]+) "([^"]+)"/g)].map((m) => [m[1], m[2]]));
delete securityHeaders["Cache-Control"];
const proxied = (path) => path.startsWith("/api/") || path === "/health" || path === "/docs" || path === "/openapi.json";

createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  if (proxied(url.pathname)) {
    const upstream = httpRequest({ host: api.hostname, port: api.port, path: url.pathname + url.search, method: req.method, headers: req.headers }, (r) => {
      res.writeHead(r.statusCode ?? 502, r.headers);
      r.pipe(res);
    });
    upstream.on("error", () => {
      res.writeHead(502);
      res.end();
    });
    req.pipe(upstream);
    return;
  }
  let file = normalize(join(root, decodeURIComponent(url.pathname)));
  if (!file.startsWith(root)) {
    res.writeHead(403);
    return res.end();
  }
  if (!existsSync(file) || statSync(file).isDirectory()) file = join(root, "index.html"); // client side route
  const ext = extname(file);
  const hashed = file.includes(`${join(root, "assets")}`);
  const headers = {
    "Content-Type": types[ext] ?? "application/octet-stream",
    "Cache-Control": hashed ? "public, max-age=31536000, immutable" : ext === ".html" ? "no-cache" : "public, max-age=3600",
    Vary: "Accept-Encoding",
    ...(ext === ".html" ? securityHeaders : { "X-Content-Type-Options": "nosniff" }),
  };
  const gzip = compressible.has(ext) && /\bgzip\b/.test(String(req.headers["accept-encoding"] ?? ""));
  if (gzip) headers["Content-Encoding"] = "gzip";
  res.writeHead(200, headers);
  const stream = createReadStream(file);
  (gzip ? stream.pipe(createGzip({ level: 9 })) : stream).pipe(res);
}).listen(port, "127.0.0.1", () => console.log(`serving dist on http://127.0.0.1:${port}`));
