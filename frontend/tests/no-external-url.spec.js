/**
 * 源码不得含外网 URL（DEV-PLAN §0）。
 * 白名单：`https://w3id.org/did/v1`（契约 DID 文档常量）；`localhost` 仅允许出现在 vite.config.js。
 */
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve, relative } from 'node:path'

const ROOT = resolve(__dirname, '..')
const SRC = join(ROOT, 'src')
const EXT = /\.(js|mjs|ts|vue|css|html|json)$/

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (EXT.test(name)) out.push(p)
  }
  return out
}

const URL_RE = /https?:\/\/[^\s'"`)<>]+/g
const ALLOW = [/^https:\/\/w3id\.org\/did\/v1$/]

describe('源码外网 URL 扫描', () => {
  const files = walk(SRC)
  it('src/ 下存在源码文件', () => expect(files.length).toBeGreaterThan(10))

  it('src/** 不含 http(s):// 外网地址（白名单除外）', () => {
    const hits = []
    for (const f of files) {
      const text = readFileSync(f, 'utf8')
      text.split('\n').forEach((line, i) => {
        const m = line.match(URL_RE)
        if (!m) return
        for (const u of m) {
          const clean = u.replace(/[.,;]+$/, '')
          if (ALLOW.some(re => re.test(clean))) continue
          // 注释中的 W3C 命名空间之类也一律不允许，保持规则简单严格
          hits.push(`${relative(ROOT, f)}:${i + 1}: ${clean}`)
        }
      })
    }
    expect(hits, `发现外网 URL：\n${hits.join('\n')}`).toEqual([])
  })

  it('src/** 不含指向 localhost 的 URL（如 http://localhost:8000；仅 vite.config.js 代理允许）', () => {
    // 只检查 URL 形式（协议或端口），mock 里作为"客户端 IP"数据值的 127.0.0.1 不算
    const re = /(wss?|https?):\/\/(localhost|127\.0\.0\.1)|(localhost|127\.0\.0\.1):\d{2,5}/
    const hits = []
    for (const f of files) {
      const text = readFileSync(f, 'utf8')
      text.split('\n').forEach((line, i) => {
        if (re.test(line)) hits.push(`${relative(ROOT, f)}:${i + 1}: ${line.trim()}`)
      })
    }
    expect(hits, `发现 localhost URL 硬编码：\n${hits.join('\n')}`).toEqual([])
  })

  it('index.html 不引用 CDN', () => {
    const html = readFileSync(join(ROOT, 'index.html'), 'utf8')
    expect(html).not.toMatch(/https?:\/\//)
  })
})
