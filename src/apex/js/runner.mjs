// Apex's Node worker: runs the real JavaScript / TypeScript functions for refutation, fuzzing and
// the fidelity check. One JSON request per line on stdin, one JSON response per line on stdout.
//
//   {"op": "load", "path": "/abs/module.ts"}                      -> {"ok": true, "exports": [...]}
//   {"op": "call", "fn": "clamp", "args": [...], "kinds": [...]}  -> {"ok": true, "value": ...,
//                                                                    "after": [...] | null,
//                                                                    "unsafe": bool}
//   failures                                                     -> {"ok": false, "kind": ...,
//                                                                    "error": "TypeError: ..."}
//
// Values cross the boundary as JSON with tags: {"$big": "123"} for bigint, {"$float": "NaN"} for
// non-integral or non-finite numbers. `kinds` says, per argument, whether integers become
// `number` or `bigint` (e.g. {"k": "list", "a": [{"k": "bigint"}]}). TypeScript runs through
// Node's built-in type stripping, so no npm package is involved.
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

let mod = null;

function toJs(v, kind) {
  if (v === null) return null;
  if (Array.isArray(v)) {
    const inner = kind && kind.k === "list" ? kind.a[0] : null;
    const parts = kind && kind.k === "tuple" ? kind.a : null;
    return v.map((x, i) => toJs(x, parts ? parts[i] : inner));
  }
  if (typeof v === "object" && "$big" in v) return BigInt(v.$big);
  if (typeof v === "number" || (typeof v === "object" && "$int" in v)) {
    const n = typeof v === "number" ? v : v.$int;
    return kind && kind.k === "bigint" ? BigInt(n) : Number(n);
  }
  return v;
}

let unsafe = false;

function fromJs(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "bigint") return { $big: v.toString() };
  if (typeof v === "number") {
    if (Number.isInteger(v)) {
      if (!Number.isSafeInteger(v)) unsafe = true;
      return Number.isSafeInteger(v) ? v : { $big: BigInt(v).toString() };
    }
    return { $float: String(v) };
  }
  if (typeof v === "boolean" || typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(fromJs);
  return { $other: String(v) };
}

function snapshot(args) {
  return JSON.stringify(args, (_, x) => (typeof x === "bigint" ? `${x}n` : x));
}

async function handle(req) {
  if (req.op === "load") {
    mod = await import(pathToFileURL(req.path).href);
    const exports = Object.keys(mod).filter((k) => typeof mod[k] === "function");
    if (mod.default && typeof mod.default === "object") {
      for (const k of Object.keys(mod.default)) {
        if (typeof mod.default[k] === "function" && !exports.includes(k)) exports.push(k);
      }
    }
    return { ok: true, exports };
  }
  if (req.op === "call") {
    const fn = (mod && mod[req.fn]) || (mod && mod.default && mod.default[req.fn]);
    if (typeof fn !== "function") {
      return { ok: false, kind: "missing", error: `${req.fn} is not exported` };
    }
    const args = req.args.map((a, i) => toJs(a, req.kinds ? req.kinds[i] : null));
    const before = snapshot(args);
    unsafe = false;
    try {
      const value = fromJs(fn(...args));
      const changed = snapshot(args) !== before;
      return { ok: true, value, after: changed ? args.map(fromJs) : null, unsafe };
    } catch (e) {
      const name = (e && e.name) || "Error";
      const message = (e && e.message) || String(e);
      const kind = name === "RangeError" && /call stack/i.test(message) ? "recursion" : "error";
      return { ok: false, kind, error: `${name}: ${message}` };
    }
  }
  return { ok: false, kind: "protocol", error: `unknown op ${req.op}` };
}

const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  let out;
  try {
    out = await handle(JSON.parse(line));
  } catch (e) {
    out = { ok: false, kind: "load", error: `${(e && e.name) || "Error"}: ${(e && e.message) || e}` };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
}
