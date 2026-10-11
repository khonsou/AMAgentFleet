#!/usr/bin/env node
/**
 * DAO（angrymiao-auth）本地 mock —— 端到端验证员工 OAuth 登录链路。
 *
 * 真实 DAO 的契约（timeline docs/oauth-auth-integration.md）：
 *   GET  /oauth/authorize           授权页（mock 自动批准，302 回 redirect_uri?code&state）
 *   POST /api/oauth/token           code exchange（校验 PKCE S256），返回 RS256 JWT
 *   JWT claims：iss / user_id(int) / role / exp / iat，RSA 公钥 PEM 验签
 *
 * 用法：
 *   node scripts/mock-oauth.mjs [port]        默认 8111
 * 启动时生成一次性 RSA 密钥对，公钥写到 /tmp/fleet-mock-oauth-pubkey.pem，
 * 控制台打印 console-backend 需要的环境变量。仅用于本地验证，严禁接入真实数据。
 */
import { createServer } from 'node:http'
import { generateKeyPairSync, createSign, createHash, randomUUID } from 'node:crypto'
import { writeFileSync } from 'node:fs'

const PORT = Number(process.argv[2] ?? 8111)
const ISSUER = process.env.MOCK_OAUTH_ISSUER || 'mock-dao'

const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
const pubPem = publicKey.export({ type: 'spki', format: 'pem' })
const PUB_PATH = '/tmp/fleet-mock-oauth-pubkey.pem'
writeFileSync(PUB_PATH, pubPem)

const b64url = (buf) => Buffer.from(buf).toString('base64url')
const codes = new Map() // code -> { challenge, clientId }

function signJwt(claims) {
  const header = b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))
  const payload = b64url(JSON.stringify(claims))
  const sig = createSign('RSA-SHA256').update(`${header}.${payload}`).sign(privateKey)
  return `${header}.${payload}.${b64url(sig)}`
}

const server = createServer((req, res) => {
  const u = new URL(req.url, `http://127.0.0.1:${PORT}`)
  // 本地 mock 宽容 CORS：真实 DAO 为注册的 origin 配 CORS，mock 用 * 即可
  res.setHeader('access-control-allow-origin', '*')
  res.setHeader('access-control-allow-headers', 'content-type')
  res.setHeader('access-control-allow-methods', 'GET,POST,OPTIONS')
  if (req.method === 'OPTIONS') { res.writeHead(204); return res.end() }
  if (u.pathname === '/pubkey.pem') {
    res.writeHead(200, { 'content-type': 'application/x-pem-file' })
    return res.end(pubPem)
  }
  if (u.pathname === '/oauth/authorize') {
    const redirect = u.searchParams.get('redirect_uri')
    const state = u.searchParams.get('state') ?? ''
    const challenge = u.searchParams.get('code_challenge') ?? ''
    if (!redirect) { res.writeHead(400); return res.end('missing redirect_uri') }
    const code = `mock-${randomUUID()}`
    codes.set(code, { challenge })
    const sep = redirect.includes('?') ? '&' : '?'
    res.writeHead(302, { location: `${redirect}${sep}code=${code}&state=${encodeURIComponent(state)}` })
    return res.end()
  }
  if (u.pathname === '/api/oauth/token' && req.method === 'POST') {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', () => {
      const body = new URLSearchParams(raw)
      const code = body.get('code') ?? ''
      const verifier = body.get('code_verifier') ?? ''
      const rec = codes.get(code)
      if (!rec || body.get('grant_type') !== 'authorization_code') {
        res.writeHead(400, { 'content-type': 'application/json' })
        return res.end(JSON.stringify({ error: 'invalid_grant' }))
      }
      codes.delete(code) // code 一次性
      // PKCE S256 校验
      const calc = b64url(createHash('sha256').update(verifier).digest())
      if (rec.challenge && calc !== rec.challenge) {
        res.writeHead(400, { 'content-type': 'application/json' })
        return res.end(JSON.stringify({ error: 'invalid_code_verifier' }))
      }
      const now = Math.floor(Date.now() / 1000)
      const token = signJwt({
        iss: ISSUER, user_id: 42, role: 'user', scope: 'profile phone',
        iat: now, exp: now + 3600,
      })
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({
        access_token: token, token_type: 'Bearer',
        expires_in: 3600, refresh_token: 'mock-refresh', refresh_token_expires_in: 2592000,
        scope: 'profile phone',
      }))
    })
    return
  }
  res.writeHead(404); res.end('mock-oauth: not found')
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`mock-oauth listening http://127.0.0.1:${PORT} (issuer=${ISSUER})`)
  console.log(`公钥已写入 ${PUB_PATH}`)
  console.log('console-backend 环境：')
  console.log(`  FLEET_AUTH_ENABLED=1`)
  console.log(`  FLEET_AUTH_JWT_PUBLIC_KEY_PATH=${PUB_PATH}`)
  console.log(`  FLEET_AUTH_JWT_ISSUER=${ISSUER}`)
  console.log('前端环境：VITE_AUTH_ENABLED=1 VITE_AUTH_ORIGIN=http://127.0.0.1:' + PORT + ' VITE_OAUTH_CLIENT_ID=agent-fleet')
})
