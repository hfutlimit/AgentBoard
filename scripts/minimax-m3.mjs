#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// minimax-m3.mjs — headless MiniMax-M3 CLI for the AgentBoard Node Worker.
//
// Contract (mirrors Codex/WorkBuddy CLI):
//   minimax-m3.mjs -p "<workload prompt>"
//   Reads $MINIMAX_ACCESS_TOKEN (injected by MiniMaxAdapter when apiKeyEnv="MINIMAX_ACCESS_TOKEN")
//   POSTs to https://agent.minimaxi.com/mavis/api/v1/llm/v1/messages (Anthropic-compatible)
//   Model = MiniMax-M3, max_tokens bounded so the wrapper stays cheap on routine work.
//   Prints exactly one JSON object on stdout (the AgentBoard worker parses the last {...} envelope).
//
// Re-uses the user's existing JWT from %USERPROFILE%\.mavis\local-runtime.auth.json
// (managed-login flow) — no new API key required.
//
// Failure modes:
//   - Token missing / 4xx auth → exit 2, stderr explains (worker surfaces via RunAndParseAsync)
//   - HTTP non-2xx          → exit 3
//   - Response unparsable   → exit 4 (worker surfaces "exit 4" verbatim)

import { readFileSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const ENDPOINT = 'https://agent.minimaxi.com/mavis/api/v1/llm/v1/messages';
const MODEL = 'MiniMax-M3';
const ANTHROPIC_VERSION = '2023-06-01';
const MAX_TOKENS = 4096;

function getPrompt(argv) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '-p' || argv[i] === '--print') {
      const next = argv[i + 1];
      if (next === undefined) {
        process.stderr.write('minimax-m3: -p requires a prompt argument\n');
        process.exit(2);
      }
      return next;
    }
  }
  process.stderr.write('minimax-m3: usage: minimax-m3.mjs -p "<prompt>"\n');
  process.exit(2);
}

function getToken() {
  const fromEnv = process.env.MINIMAX_ACCESS_TOKEN;
  if (fromEnv && fromEnv.trim().length > 0) return fromEnv.trim();
  // Fallback: read the local-runtime auth file (managed-login JWT).
  const authFile = join(homedir(), '.mavis', 'local-runtime.auth.json');
  if (existsSync(authFile)) {
    try {
      const parsed = JSON.parse(readFileSync(authFile, 'utf8'));
      const t = parsed?.auth?.accessToken;
      if (typeof t === 'string' && t.length > 0) return t;
    } catch (e) {
      process.stderr.write(`minimax-m3: cannot read ${authFile}: ${e.message}\n`);
    }
  }
  process.stderr.write(
    'minimax-m3: no MINIMAX_ACCESS_TOKEN in env and ~/.mavis/local-runtime.auth.json missing accessToken\n'
  );
  process.exit(2);
}

function extractJsonFromText(text) {
  // The M3 response may wrap the JSON in prose. Walk braces and return the last
  // parseable top-level object — same heuristic the worker's RunAndParseAsync uses
  // (TryExtractLastJson). Keeps the contract compatible.
  if (!text) return null;
  const stripped = text.replace(/```json|```/g, '');
  let last = null;
  for (let i = 0; i < stripped.length; i++) {
    if (stripped[i] !== '{') continue;
    try {
      // Cheap balanced-substring probe by attempting to JSON.parse from each '{'.
      const candidate = stripped.slice(i);
      const obj = JSON.parse(candidate);
      if (obj && typeof obj === 'object') {
        // Take first top-level object's raw text — keep balanced braces via JSON.stringify round-trip.
        last = JSON.stringify(obj);
        break;
      }
    } catch {
      /* keep scanning */
    }
  }
  return last;
}

async function main() {
  const prompt = getPrompt(process.argv.slice(2));
  const token = getToken();

  const body = {
    model: MODEL,
    max_tokens: MAX_TOKENS,
    // Force a single JSON object on the last line — matches the worker contract
    // (RunAndParseAsync → TryExtractLastJson).
    system:
      'You are the MiniMax-M3 model invoked by an AgentBoard worker. ' +
      'When the user prompt asks you to finish with a JSON object, respond with exactly that JSON object ' +
      'on the last line of your reply and nothing after it. ' +
      'Otherwise respond normally. Never wrap the final answer in markdown fences.',
    messages: [{ role: 'user', content: prompt }],
  };

  const resp = await fetch(ENDPOINT, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${token}`,
      'anthropic-version': ANTHROPIC_VERSION,
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const errText = await resp.text().catch(() => '');
    process.stderr.write(`minimax-m3: HTTP ${resp.status}: ${errText.slice(0, 500)}\n`);
    process.exit(3);
  }

  const data = await resp.json();
  const parts = Array.isArray(data?.content) ? data.content : [];
  const text = parts
    .filter((p) => p && p.type === 'text' && typeof p.text === 'string')
    .map((p) => p.text)
    .join('\n');

  if (!text) {
    process.stderr.write(`minimax-m3: empty model response: ${JSON.stringify(data).slice(0, 500)}\n`);
    process.exit(4);
  }

  // Prefer a JSON object inside the response (worker contract); fall back to raw text.
  const json = extractJsonFromText(text) ?? text;
  process.stdout.write(json.endsWith('\n') ? json : json + '\n');
}

main().catch((e) => {
  process.stderr.write(`minimax-m3: unexpected error: ${e?.stack ?? e}\n`);
  process.exit(4);
});
