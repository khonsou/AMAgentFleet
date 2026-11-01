import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = resolve(process.cwd(), 'src')
const PROTOCOL = resolve(process.cwd(), '../../fleet-ops-protocol.json')

function productionFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`
    if (entry.isDirectory()) return productionFiles(path)
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : []
  })
}

describe('Protocol Client 防回退', () => {
  it('页面、组件和旧 API 层不得直接 fetch Fleet 业务端点', () => {
    const files = [
      ...productionFiles(`${SRC}/pages`),
      ...productionFiles(`${SRC}/components`),
      `${SRC}/lib/api.ts`,
    ]
    const offenders = files.filter((path) => /\bfetch\s*\(/.test(readFileSync(path, 'utf8')))
    expect(offenders).toEqual([])
  })

  it('旧 API 层的 operation ID 与读写属性均已在协议中登记', () => {
    const source = readFileSync(`${SRC}/lib/api.ts`, 'utf8')
    const protocol = JSON.parse(readFileSync(PROTOCOL, 'utf8'))
    const operations = new Map(protocol.operations.map((operation: { id: string }) => [operation.id, operation]))
    const calls = [...source.matchAll(/\b(read|write)<[^\n]+?>\('([^']+)'/g)]
    expect(calls.length).toBeGreaterThan(30)
    for (const [, kind, operationId] of calls) {
      const operation = operations.get(operationId) as { write?: boolean } | undefined
      expect(operation, `未登记 operation: ${operationId}`).toBeDefined()
      if (kind === 'write') expect(operation?.write, `${operationId} 必须声明 write`).toBe(true)
    }
  })
})
