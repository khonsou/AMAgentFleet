# 本地预览与全页数据加载失败排障

## 典型症状

首页 Task、Playground Agent、Registry、Threads、Settings 同时失败，页面显示 `Error: null`，或操作助手提示暂时不可用。这通常不是各页面分别发生数据或渲染回归，而是**只有 Vite 前端仍在运行，8001／8002／8003 后端未启动**。

`Error: null` 的成因是代理或后端返回空错误体，旧 Protocol Client 直接把 `null` 当成错误文本。当前实现已改为显示 HTTP 状态及 `8001/8002/8003` 启动提示；网络连接失败会明确提示“不能只有前端 Vite”。

## 一键检查

```bash
cd console
npm run check:stack
# 或指定实际预览地址
/bin/bash scripts/check-stack.sh http://localhost:7102/
```

脚本只读检查，不启动或停止进程，依次验证：

- Gateway `:8001/health`
- Console backend `:8002/api/console/health`
- Control Plane `:8003/api/health`
- Runtime capabilities `:8003/api/runtime/capabilities`
- 前端预览地址

任一失败即退出非零并打印缺失服务。

## 恢复步骤

1. 从项目根目录启动**完整环境**，不要只运行 `dev:vite`：

   ```bash
   cd console
   npm run dev
   ```

2. 等待 `npm run check:stack` 全部显示 `OK`，再刷新页面。
3. 若页面仍显示旧错误，强制刷新；不要先改业务代码或清空数据库。
4. 若端口被占用，用 `lsof -nP -iTCP:<port> -sTCP:LISTEN` 确认进程归属，只停止对应 PID，禁止模糊 `pkill -f`。
5. Python 服务没有热更新：修改 `control-plane/` 或 `console/backend/` 后必须重启 8003／8002；Vite 变新不代表后端变新。

## 防止误判与回归

- 构建成功、单元测试通过或 Vite 页面能打开，都不能证明完整环境可运行。
- 验收至少覆盖：首页 Task API 200、Registry Agent 列表、Thread 列表、Settings、Playground Agent 数量、Task → Loop → Thread 实际点击，以及操作助手创建／发送／关闭／刷新恢复。
- 完整在线验收：`bash scripts/test_fleet_ops_all.sh`；离线回归：`bash scripts/test_fleet_ops_all.sh --offline`。
- 操作助手写操作需要统一确认。确认框层级必须高于浮窗，否则按钮会被遮挡；当前固定为 `z-index: 1000/1001`，并由桌面和移动浏览器用例覆盖。
- 测试结束后如果只停止后端而留下 Vite，页面会再次出现全页失败。重新通过完整预览入口或 `npm run dev` 启动，而不是只恢复前端。

相关回归位于 `console/frontend/src/protocol/client.test.ts`（空错误体与网络失败提示）和 `console/frontend/e2e/fleet-ops-widget.spec.ts`（操作助手完整交互）。
