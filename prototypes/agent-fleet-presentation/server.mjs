import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(fileURLToPath(import.meta.url))
const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.wasm': 'application/wasm',
}

export async function startServer(port = 7100, host = '127.0.0.1') {
  const server = http.createServer(async (request, response) => {
    try {
      if (!['GET', 'HEAD'].includes(request.method)) {
        response.writeHead(405).end('Method Not Allowed')
        return
      }
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
      const relativePath = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '')
      let filePath = path.resolve(root, relativePath)
      if (filePath !== root && !filePath.startsWith(`${root}${path.sep}`)) {
        response.writeHead(403).end('Forbidden')
        return
      }
      let info = await stat(filePath)
      if (info.isDirectory()) {
        filePath = path.join(filePath, 'index.html')
        info = await stat(filePath)
      }
      response.writeHead(200, {
        'Content-Type': mimeTypes[path.extname(filePath)] || 'application/octet-stream',
        'Content-Length': info.size,
        'Cache-Control': 'no-store',
      })
      if (request.method === 'HEAD') response.end()
      else createReadStream(filePath).pipe(response)
    } catch (error) {
      response.writeHead(error.code === 'ENOENT' ? 404 : 500).end(error.code === 'ENOENT' ? 'Not Found' : 'Server Error')
    }
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, host, resolve)
  })
  return server
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  const readArg = (name, fallback) => {
    const index = args.indexOf(name)
    return index >= 0 && args[index + 1] ? args[index + 1] : fallback
  }
  const port = Number(readArg('--port', process.env.PORT || 7100))
  const hostFlag = readArg('--host', '127.0.0.1')
  const host = hostFlag === 'true' ? '0.0.0.0' : hostFlag
  const server = await startServer(port, host)
  console.log(`Fleet presentation prototype: http://${host}:${server.address().port}`)
}
