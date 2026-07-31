/**
 * Generates shiki-classes.css from the tokyo-night theme.
 *
 * createStyleToClassTransformer replaces every inline style attribute with a
 * stable CSS class whose name is a cyrb53 hash of the style string.
 * Build-time and runtime instances produce identical names because the hash is
 * a pure function of the style string content.
 *
 * Run this script whenever shiki or @shikijs/themes is upgraded.
 *
 * Usage: mise exec -- node frontend/scripts/gen-shiki-css.mjs
 */

// cyrb53: public-domain 53-bit hash — identical to the one inside @shikijs/transformers.
function cyrb53(str, seed = 0) {
	let h1 = 0xdeadbeef ^ seed,
		h2 = 0x41c6ce57 ^ seed;
	for (let i = 0, ch; i < str.length; i++) {
		ch = str.charCodeAt(i);
		h1 = Math.imul(h1 ^ ch, 2654435761);
		h2 = Math.imul(h2 ^ ch, 1597334677);
	}
	h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
	h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
	h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
	h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
	return 4294967296 * (2097151 & h2) + (h1 >>> 0);
}

function createStyleToClassTransformer() {
	const registry = new Map(); // style string → class name

	function register(style) {
		if (!style) return null;
		const s = typeof style === 'string' ? style : String(style);
		if (registry.has(s)) return registry.get(s);
		const cls = `__s_${cyrb53(s).toString(16)}`;
		registry.set(s, cls);
		return cls;
	}

	return {
		name: 'style-to-class',
		pre(t) {
			if (!t.properties?.style) return;
			const cls = register(t.properties.style);
			if (!cls) return;
			delete t.properties.style;
			this.addClassToHast(t, cls);
		},
		span(t) {
			if (!t.properties?.style) return;
			const cls = register(t.properties.style);
			if (!cls) return;
			delete t.properties.style;
			t.properties.class = t.properties.class ? `${t.properties.class} ${cls}` : cls;
		},
		getCSS() {
			return [...registry.entries()].map(([style, cls]) => `.${cls}{${style}}`).join('\n');
		},
		entries() {
			return registry.entries();
		},
	};
}

/**
 * Tokyo Night (dark) hex → light-theme replacement, keyed by uppercase hex
 * without "#". Values are either a literal hex sourced from the official
 * "Tokyo Night Light" VS Code theme (enkia/tokyo-night-vscode-theme), or a
 * `var(--xxx)` reference where the dark hex is an exact match for one of
 * this project's existing dark-theme CSS variables — reusing the
 * already-contrast-checked light value of that same variable instead of
 * inventing a new color.
 */
const DARK_TO_LIGHT = {
	'1A1B26': 'var(--bg-alt)', // pre background — exact hex match of project --bg (dark), but reusing --bg here would make the code block fill identical to the page background in light theme (no visible panel); --bg-alt gives it a real, separate surface
	'51597D': 'var(--fg-muted)', // comment — official #888B94 fails 3:1 on light bg
	'5A638C': 'var(--fg-muted)', // jsdoc comment sub-shade
	'646E9C': 'var(--fg-muted)', // jsdoc comment sub-shade
	'0DB9D7': '#006C86', // support.class/type/function (official)
	'7AA2F7': '#2959AA', // entity.name.function (official)
	'7DCFFF': 'var(--cyan)', // exact match of project --cyan (dark)
	'89DDFF': '#363C4D', // operators / fenced-code punctuation (official)
	'9AA5CE': '#40434F', // constant.other.color (official)
	'9ABDF5': '#484C61', // meta.property-list (official)
	'9D7CD8': '#65359D', // storage.modifier (official keyword/storage)
	'9ECE6A': 'var(--green)', // exact match of project --green (dark)
	A9B1D6: 'var(--fg-dark)', // exact match of project --fg-dark (dark)
	BA3C97: '#B05467', // punctuation.definition.tag (official)
	BB9AF7: 'var(--magenta)', // exact match of project --magenta (dark)
	C0CAF5: 'var(--fg)', // exact match of project editor fg / project --fg
	E0AF68: 'var(--yellow)', // exact match of project --yellow / --accent default (dark)
	F7768E: '#8C4351', // entity.name.tag / variable.language (official)
	FF9E64: 'var(--accent-warm)', // exact match of project --accent-warm (dark)
	'73DACA': '#33635C', // string.other.link / teal family (official)
};

function translate(style, map) {
	let changed = false;
	const next = style.replace(/#([0-9A-Fa-f]{6})\b/g, (full, hex) => {
		const repl = map[hex.toUpperCase()];
		if (!repl) return full;
		changed = true;
		return repl;
	});
	return changed ? next : null;
}

/**
 * Hex → CSS var translated in the *default* (dark) output too, not just the
 * `.theme-light` override — for tokens whose literal Shiki hex happens to
 * equal one of this project's own CSS vars, but where reusing that var
 * verbatim would make the token identical to the page background instead of
 * a distinct surface. Currently just the pre/code-block root background:
 * Tokyo Night's official editor bg (#1a1b26) is an exact hex match for this
 * project's dark --bg, so without this override the code block's fill is
 * indistinguishable from the surrounding article in dark mode.
 */
const ALWAYS_TRANSLATE = {
	'1A1B26': 'var(--bg-dark)', // pre background — see DARK_TO_LIGHT's 1A1B26 entry for the light-theme counterpart
};

import { createHighlighterCoreSync } from 'shiki/core';
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript';
import tokyoNight from '@shikijs/themes/tokyo-night';
import python from '@shikijs/langs/python';
import bash from '@shikijs/langs/bash';
import rust from '@shikijs/langs/rust';
import typescript from '@shikijs/langs/typescript';
import javascript from '@shikijs/langs/javascript';
import html from '@shikijs/langs/html';
import css from '@shikijs/langs/css';
import go from '@shikijs/langs/go';
import java from '@shikijs/langs/java';
import c from '@shikijs/langs/c';
import sql from '@shikijs/langs/sql';
import yaml from '@shikijs/langs/yaml';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

const OUT = join(dirname(fileURLToPath(import.meta.url)), '../src/lib/markdown/shiki-classes.css');

const transformer = createStyleToClassTransformer();

const highlighter = createHighlighterCoreSync({
	themes: [tokyoNight],
	langs: [python, bash, rust, typescript, javascript, html, css, go, java, c, sql, yaml],
	engine: createJavaScriptRegexEngine(),
});

const SAMPLES = {
	python: `\
import os
from dataclasses import dataclass
from typing import Optional

# Configuration constant
MAX_RETRIES: int = 3

@dataclass
class Config:
    """Application configuration."""
    host: str
    port: int = 8080
    debug: bool = False

def fetch_data(url: str, timeout: Optional[float] = None) -> dict:
    """Fetch remote data with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, timeout=timeout or 5.0)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(f"Failed after {MAX_RETRIES} attempts") from exc
    return {}
`,

	typescript: `\
import { EventEmitter } from 'node:events';

type Status = 'pending' | 'running' | 'done' | 'error';

interface Task<T = unknown> {
  readonly id: string;
  status: Status;
  result?: T;
}

class TaskRunner<T> extends EventEmitter {
  private readonly tasks = new Map<string, Task<T>>();

  async run(id: string, fn: () => Promise<T>): Promise<T> {
    const task: Task<T> = { id, status: 'running' };
    this.tasks.set(id, task);
    try {
      task.result = await fn();
      task.status = 'done';
      this.emit('done', task);
      return task.result;
    } catch (err: unknown) {
      task.status = 'error';
      throw err;
    }
  }
}

export { TaskRunner };
export type { Task, Status };
`,

	javascript: `\
'use strict';

const BASE_URL = 'https://api.example.com/v1';
const TIMEOUT_MS = 5_000;

/**
 * Retry an async function with exponential backoff.
 * @param {() => Promise<unknown>} fn
 * @param {number} retries
 */
async function withRetry(fn, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      return await fn();
    } catch (err) {
      if (i === retries - 1) throw err;
      await new Promise(r => setTimeout(r, 2 ** i * 100));
    }
  }
}

module.exports = { withRetry, BASE_URL, TIMEOUT_MS };
`,

	rust: `\
use std::collections::HashMap;
use std::fmt;

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Number(f64),
    Str(String),
    Array(Vec<Value>),
    Object(HashMap<String, Value>),
}

impl fmt::Display for Value {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Value::Null => write!(f, "null"),
            Value::Bool(b) => write!(f, "{b}"),
            Value::Number(n) => write!(f, "{n}"),
            Value::Str(s) => write!(f, "\"{s}\""),
            Value::Array(arr) => {
                let items: Vec<_> = arr.iter().map(|v| v.to_string()).collect();
                write!(f, "[{}]", items.join(", "))
            }
            Value::Object(_) => write!(f, "{{...}}"),
        }
    }
}
`,

	bash: `\
#!/usr/bin/env bash
set -euo pipefail

readonly LOG_FILE="/var/log/deploy.log"
readonly DEPLOY_DIR="/srv/app"

log() {
  local level="$1"; shift
  printf '[%s] %s: %s\n' "$(date -u +%FT%TZ)" "$level" "$*" | tee -a "$LOG_FILE"
}

check_deps() {
  local missing=()
  for cmd in git docker curl jq; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
  done
  if (( \${#missing[@]} > 0 )); then
    log ERROR "Missing dependencies: \${missing[*]}"
    exit 1
  fi
}

deploy() {
  local ref="\${1:-main}"
  log INFO "Deploying ref: $ref"
  git -C "$DEPLOY_DIR" fetch --quiet origin
  git -C "$DEPLOY_DIR" checkout --quiet "$ref"
  docker compose up -d --build
  log INFO "Deploy complete"
}

check_deps
deploy "\${1:-}"
`,

	go: `\
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"
)

const defaultTimeout = 30 * time.Second

var ErrNotFound = errors.New("resource not found")

type Server struct {
	addr    string
	timeout time.Duration
	mux     *http.ServeMux
}

func NewServer(addr string) *Server {
	s := &Server{addr: addr, timeout: defaultTimeout, mux: http.NewServeMux()}
	s.mux.HandleFunc("/health", s.handleHealth)
	return s
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, \`{"status":"ok"}\`)
}

func (s *Server) Run(ctx context.Context) error {
	srv := &http.Server{Addr: s.addr, Handler: s.mux, ReadTimeout: s.timeout}
	slog.Info("server starting", "addr", s.addr)
	return srv.ListenAndServe()
}
`,

	java: `\
package com.example.app;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;

/**
 * Generic repository interface for CRUD operations.
 *
 * @param <T>  entity type
 * @param <ID> identifier type
 */
public interface Repository<T, ID> {

    Optional<T> findById(ID id);

    List<T> findAll();

    T save(T entity);

    void deleteById(ID id);
}

public final class UserService {
    private static final int MAX_PAGE_SIZE = 100;
    private final Repository<User, Long> repo;

    public UserService(Repository<User, Long> repo) {
        this.repo = repo;
    }

    public CompletableFuture<Optional<User>> findByIdAsync(long id) {
        return CompletableFuture.supplyAsync(() -> repo.findById(id));
    }
}
`,

	c: `\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#define MAX_BUF 4096
#define VERSION "1.0.0"

typedef struct Node {
    int value;
    struct Node *next;
} Node;

/* Allocate a new linked-list node. Returns NULL on OOM. */
Node *node_new(int value) {
    Node *n = malloc(sizeof(Node));
    if (!n) return NULL;
    n->value = value;
    n->next  = NULL;
    return n;
}

void list_free(Node *head) {
    while (head) {
        Node *tmp = head->next;
        free(head);
        head = tmp;
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <value>\\n", argv[0]);
        return EXIT_FAILURE;
    }
    int val = (int)strtol(argv[1], NULL, 10);
    Node *list = node_new(val);
    printf("created node with value %d\\n", list->value);
    list_free(list);
    return EXIT_SUCCESS;
}
`,

	html: `\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Example Page</title>
  <link rel="stylesheet" href="/styles/main.css" />
</head>
<body>
  <header class="site-header" role="banner">
    <nav aria-label="Primary navigation">
      <ul>
        <li><a href="/" class="nav-link active">Home</a></li>
        <li><a href="/about">About</a></li>
      </ul>
    </nav>
  </header>

  <main id="content">
    <section aria-labelledby="hero-title">
      <h1 id="hero-title">Welcome</h1>
      <p>This is a <strong>sample</strong> HTML document.</p>
      <!-- TODO: add hero image -->
      <button type="button" data-action="open-modal">Get started</button>
    </section>
  </main>

  <script type="module" src="/scripts/app.js"></script>
</body>
</html>
`,

	css: `\
/* Design tokens */
:root {
  --color-bg: #1a1b26;
  --color-fg: #c0caf5;
  --color-accent: #ff757f;
  --font-sans: 'Inter', system-ui, sans-serif;
  --radius-md: 6px;
  --shadow-sm: 0 1px 3px rgb(0 0 0 / 0.3);
}

@layer base {
  *, *::before, *::after { box-sizing: border-box; }

  body {
    margin: 0;
    font-family: var(--font-sans);
    background-color: var(--color-bg);
    color: var(--color-fg);
    line-height: 1.6;
  }
}

@layer components {
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 1rem;
    border: none;
    border-radius: var(--radius-md);
    background: var(--color-accent);
    color: #fff;
    cursor: pointer;
    transition: opacity 150ms ease;

    &:hover { opacity: 0.85; }
    &:disabled { opacity: 0.5; cursor: not-allowed; }
  }
}

@media (prefers-reduced-motion: reduce) {
  * { transition-duration: 0.01ms !important; }
}
`,

	sql: `\
-- single-line comment
/* block comment */
SELECT u.id, u.name, COUNT(o.id) AS total
FROM users u
LEFT JOIN orders o ON o.user_id = u.id
WHERE u.active = TRUE
  AND u.created_at >= '2024-01-01'
GROUP BY u.id, u.name
HAVING COUNT(o.id) > 0
ORDER BY total DESC
LIMIT 10;

INSERT INTO logs (level, message) VALUES ('info', 'started');
UPDATE settings SET value = 42 WHERE key = 'timeout';
DELETE FROM sessions WHERE expires_at < NOW();
`,

	yaml: `\
# block comment
defaults: &defaults
  timeout: 30
  retries: 3
  debug: false

service:
  <<: *defaults
  name: "example"
  version: 1.0
  tags: [web, api, public]
  enabled: true
  ratio: 0.75
  nullable: null
  multiline: |
    line one
    line two
  folded: >
    folded
    text
  nested:
    key: value
    list:
      - first
      - second
`,
};

const opts = { theme: 'tokyo-night', transformers: [transformer] };
for (const [lang, code] of Object.entries(SAMPLES)) {
	highlighter.codeToHtml(code, { ...opts, lang });
}

const dark_rules = [];
for (const [style, cls] of transformer.entries()) {
	const darkStyle = translate(style, ALWAYS_TRANSLATE);
	dark_rules.push(`.${cls}{${darkStyle ?? style}}`);
}
const css_out = dark_rules.join('\n');

const light_rules = [];
for (const [style, cls] of transformer.entries()) {
	const lightStyle = translate(style, DARK_TO_LIGHT);
	if (lightStyle) light_rules.push(`.theme-light .${cls}{${lightStyle}}`);
}
const light_css_out = light_rules.join('\n');

writeFileSync(
	OUT,
	`/* Generated by scripts/gen-shiki-css.mjs — do not edit manually.\n   Re-run after upgrading shiki or @shikijs/themes. */\n\n${css_out}\n\n/* Light theme overrides — Tokyo Night Light (official) where no project\n   CSS variable applies; reused var(--xxx) where the dark hex matched one. */\n\n${light_css_out}\n`
);

console.log(
	`Written ${OUT} (${css_out.length + light_css_out.length} bytes, ${css_out.split('\n').length} dark rules, ${light_rules.length} light overrides)`
);
