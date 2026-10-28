import { Route, Routes } from 'react-router'
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

export default function App() {
  return (
    <div className="min-h-screen bg-chassis text-ink">
      <Rack />
      <Routes>
        {/* OAuth 回调在登录门之外：完成 exchange 才建立会话 */}
        <Route path="/oauth/callback" element={<AuthCallbackPage />} />
        <Route path="/*" element={
          <AuthGate>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/registry" element={<RegistryPage />} />
              <Route path="/threads" element={<ThreadsPage />} />
              <Route path="/agents/:name" element={<AgentDetailPage />} />
              <Route path="/tasks/:tid" element={<TaskDetailPage />} />
              <Route path="/threads/:tid" element={<ThreadDetailPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
            <FleetOpsWidget />
          </AuthGate>
        } />
      </Routes>
    </div>
  )
}
