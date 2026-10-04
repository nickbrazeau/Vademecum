// Fold the in-chat build into one self-contained HTML document and place it
// where the MCP server serves it from (ADR 0014). Run after
// `vite build --config vite.app.config.ts`.
//
// Also makes the host's theme choice effective: the stylesheet switches dark
// variables on the system preference, which a sandboxed frame may not follow;
// the same variable blocks are repeated under `:root[data-theme=...]` so the
// host can say which it is.

import { readFileSync, writeFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const dist = join(here, '..', 'dist-app')
const target = join(here, '..', '..', 'mcp', 'src', 'vademecum_mcp', 'widgets', 'app.html')

const files = readdirSync(dist)
const jsName = files.find((name) => name.endsWith('.js'))
const cssName = files.find((name) => name.endsWith('.css'))
if (!jsName) throw new Error('no script in dist-app')

let html = readFileSync(join(dist, 'app.html'), 'utf8')
const js = readFileSync(join(dist, jsName), 'utf8')
let css = cssName ? readFileSync(join(dist, cssName), 'utf8') : ''

if (/\bimport\s*\(|^\s*import\s/m.test(js.slice(0, 2000)) && /from\s*["']\.\//.test(js)) {
  throw new Error('the in-chat bundle still imports another chunk; it must be one file')
}
if (/<\/script/i.test(js)) throw new Error('the bundle contains a closing script tag')

// Dark blocks: `@media (prefers-color-scheme: dark) { :root { ... } }` →
// also `:root[data-theme="dark"] { ... }`. Light: the base `:root { ... }`
// blocks → also `:root[data-theme="light"] { ... }`.
const darkBlocks = [...css.matchAll(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^}]*)\}\s*\}/g)].map((m) => m[1])
const lightBlocks = [...css.matchAll(/(?<![\w\-\]])\:root\s*\{([^}]*)\}/g)]
  .map((m) => m[1])
  .filter((body) => !darkBlocks.includes(body))
css += '\n' + darkBlocks.map((body) => `:root[data-theme="dark"]{${body}}`).join('\n')
css += '\n' + lightBlocks.map((body) => `:root[data-theme="light"]{${body}}`).join('\n')
// Inside a conversation the frame is the page: no outer gutter, no min height.
css += '\n:root[data-host="chat"] body{min-height:0}'

html = html
  .replace(/<link rel="stylesheet"[^>]*>/g, '')
  .replace(/<script type="module"[^>]*><\/script>/g, '')
  // Function replacements: a string replacement expands `$&`, `$'` and the
  // like, and a minified bundle can contain them (a variable named `$`).
  .replace('</head>', () => `<style>\n${css}\n</style>\n</head>`)
  .replace('</body>', () => `<script type="module">\n${js}\n</script>\n</body>`)

if (/<script[^>]*\ssrc=|<link\s/i.test(html)) throw new Error('the document still references a file')

if (darkBlocks.length === 0) throw new Error('no dark theme block found in the stylesheet')
writeFileSync(target, html)
console.log(`wrote ${target} (${(html.length / 1024).toFixed(0)} KB)`)
