import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'
import Rack from './components/console/Rack'
import AuthGate from './components/console/AuthGate'
import Home from './pages/Home'
import RegistryPage from './pages/Registry'
import ThreadsPage from './pages/Threads'
import ThreadDetailPage from './pages/ThreadDetail'
import AgentDetailPage from './pages/AgentDetail'
import TaskDetailPage from './pages/TaskDetail'
import SettingsPage from './pages/Settings'
import AuthCallbackPage from './pages/AuthCallback'
import FleetOpsWidget from './components/console/FleetOpsWidget'
import { OperationProvider } from './protocol/OperationProvider'

const PlaygroundPage = lazy(() => import('./playground/Playground'))

export default function App() {
  const location = useLocation()
  const isPlayground = location.pathname === '/' || location.pathname.startsWith('/playground')
  return (
    <div className="min-h-screen bg-chassis text-ink">
      <Rack />
      <Routes>
        {/* OAuth 回调在登录门之外：完成 exchange 才建立会话 */}
        <Route path="/oauth/callback" element={<AuthCallbackPage />} />
        <Route path="/*" element={
          <AuthGate>
            <OperationProvider>
              <Routes>
                <Route path="/" element={<Suspense fallback={<div className="p-7 font-mono text-xs">Loading Playground…</div>}><PlaygroundPage /></Suspense>} />
                <Route path="/playground" element={<Suspense fallback={<div className="p-7 font-mono text-xs">Loading Playground…</div>}><PlaygroundPage /></Suspense>} />
                <Route path="/fleet" element={<Navigate to="/tasks" replace />} />
                <Route path="/tasks" element={<Home />} />
                <Route path="/registry" element={<RegistryPage />} />
                <Route path="/threads" element={<ThreadsPage />} />
                <Route path="/agents/:name" element={<AgentDetailPage />} />
                <Route path="/tasks/:tid" element={<TaskDetailPage />} />
                <Route path="/threads/:tid" element={<ThreadDetailPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
              {!isPlayground && <FleetOpsWidget />}
            </OperationProvider>
          </AuthGate>
        } />
      </Routes>
    </div>
  )
}
