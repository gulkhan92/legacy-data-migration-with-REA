#!/usr/bin/env node
// Thin wrapper around the pinned REA command line tool.
//
//   node wrapper.mjs <command> <absolute-file-path> [options]
//
// Accepts one whitelisted static analysis command, one file and a few whitelisted options, runs the
// REA CLI with JSON output, and prints one JSON envelope on stdout:
//
//   { "ok": true,  "command": "...", "rea_version": "...", "duration_ms": 123, "result": { ... } }
//   { "ok": false, "command": "...", "rea_version": "...", "duration_ms": 123, "error": { "kind", "message" } }
//
// Only static analysis commands are allowed. Nothing here loads or executes the analysed file, and
// the wrapper never uses a shell. Runtime capture, debugging and network features are not exposed.
//
// Environment: REA_TIMEOUT_SECONDS (default 120), REA_ALLOWED_ROOTS (path list separator separated,
// default: any absolute path), REA_MAX_OUTPUT_BYTES (default 67108864).

import { execFile } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { isAbsolute, delimiter, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const REA_ROOT = join(HERE, "node_modules", "rea-agents");
const REA_BIN = join(REA_ROOT, "scripts", "rea.mjs");

// command -> options it accepts. Values: "value" (one argument), "repeat" (may be given many times).
const COMMANDS = {
  "inspect-sqlite-database": { "--table": "value", "--column": "repeat", "--row-limit": "value", "--schema-scope": "value" },
  "inspect-managed-artifact": {},
  "inspect-managed-members": { "--type": "repeat" },
};
const SCHEMA_SCOPES = new Set(["inventory", "selected_table", "database"]);

const timeoutSeconds = Number(process.env.REA_TIMEOUT_SECONDS ?? 120);
const maxOutput = Number(process.env.REA_MAX_OUTPUT_BYTES ?? 64 * 1024 * 1024);
const allowedRoots = (process.env.REA_ALLOWED_ROOTS ?? "").split(delimiter).filter(Boolean).map((p) => resolve(p));

function reaVersion() {
  try {
    return JSON.parse(readFileSync(join(REA_ROOT, "package.json"), "utf8")).version;
  } catch {
    return null;
  }
}

function finish(envelope, started) {
  envelope.duration_ms = Math.round(performance.now() - started);
  envelope.rea_version = reaVersion();
  // Exit only after stdout has flushed: large results written to a pipe are otherwise cut off.
  process.stdout.write(JSON.stringify(envelope) + "\n", () => process.exit(envelope.ok ? 0 : 1));
}

function fail(command, kind, message, started) {
  finish({ ok: false, command, error: { kind, message } }, started);
}

function parseArgs(argv) {
  const [command, file, ...rest] = argv;
  const spec = COMMANDS[command];
  if (!spec) return { error: ["unknown_command", `Command is not allowed: ${command ?? "(none)"}`] };
  if (!file || !isAbsolute(file)) return { error: ["invalid_arguments", "The file must be an absolute path"] };
  const args = [];
  for (let i = 0; i < rest.length; i += 1) {
    const flag = rest[i];
    const kind = spec[flag];
    if (!kind) return { error: ["invalid_arguments", `Option is not allowed for ${command}: ${flag}`] };
    const value = rest[i + 1];
    if (value === undefined || value.startsWith("--")) return { error: ["invalid_arguments", `Missing value for ${flag}`] };
    if (flag === "--row-limit" && !/^[1-9][0-9]{0,3}$/.test(value)) return { error: ["invalid_arguments", "--row-limit must be 1 to 9999"] };
    if (flag === "--schema-scope" && !SCHEMA_SCOPES.has(value)) return { error: ["invalid_arguments", "Unknown schema scope"] };
    args.push(flag, value);
    i += 1;
  }
  return { command, file, args };
}

const started = performance.now();
const parsed = parseArgs(process.argv.slice(2));
if (parsed.error) fail(process.argv[2] ?? null, parsed.error[0], parsed.error[1], started);

const { command, file, args } = parsed;
const resolved = resolve(file);
if (allowedRoots.length && !allowedRoots.some((root) => resolved === root || resolved.startsWith(root + sep))) {
  fail(command, "path_not_allowed", "The file is outside the allowed directories", started);
}
try {
  if (!statSync(resolved).isFile()) throw new Error("not a file");
} catch {
  fail(command, "file_not_found", "The file does not exist or is not a regular file", started);
}

// A minimal environment: no inherited secrets, no proxy settings. HOME points at a scratch directory.
const env = { PATH: process.env.PATH ?? "", HOME: process.env.HOME && process.env.HOME !== "/" ? process.env.HOME : tmpdir(), NO_COLOR: "1" };

execFile(
  process.execPath,
  [REA_BIN, command, resolved, ...args, "--json"],
  { timeout: timeoutSeconds * 1000, killSignal: "SIGKILL", maxBuffer: maxOutput, env, windowsHide: true, shell: false },
  (error, stdout, stderr) => {
    if (error) {
      if (error.killed || error.signal === "SIGKILL") return fail(command, "timeout", `Analysis exceeded ${timeoutSeconds} seconds`, started);
      if (error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER") return fail(command, "output_too_large", "The analysis output exceeded the size limit", started);
      const detail = String(stderr || stdout || "").trim().slice(0, 2000);
      return fail(command, "analysis_failed", detail || `The analysis tool exited with code ${error.code}`, started);
    }
    try {
      return finish({ ok: true, command, result: JSON.parse(stdout) }, started);
    } catch {
      return fail(command, "invalid_output", "The analysis tool did not return valid JSON", started);
    }
  },
);
