import './App.css'
import { lazy, Suspense, useEffect, useState } from 'react'
import { BrowserRouter, HashRouter, Navigate, Routes, Route } from 'react-router-dom'
import { useTaskPolling } from '@/hooks/useTaskPolling.ts'
import { useCheckBackend } from '@/hooks/useCheckBackend.ts'
import { systemCheck } from '@/services/system.ts'
import BackendInitDialog from '@/components/BackendInitDialog'
import StartupBanner from '@/components/SystemDiagnostic/StartupBanner'
import BackendHealthIndicator from '@/components/BackendHealth/BackendHealthIndicator'
import Index from '@/pages/Index.tsx'
import { HomePage } from './pages/HomePage/Home.tsx'
import { useAuthStore } from '@/store/authStore'
import LoginPage from '@/pages/Login'
import { useTaskStore } from '@/store/taskStore'
import { importLegacyBrowserData } from '@/services/userData'
import { Button } from '@/components/ui/button'

// 非首屏页面使用 React.lazy 按需加载
const Onboarding = lazy(() => import('@/pages/Onboarding'))
const SettingPage = lazy(() => import('./pages/SettingPage/index.tsx'))

// 桌面端首启引导守卫：未完成 onboarding 时强制跳到 /onboarding
function OnboardingGuard({ children }: { children: React.ReactNode }) {
  const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  // 仅在 Tauri 桌面端拦截；纯 web 端不打扰用户
  const isAdmin = useAuthStore(state => state.user?.role === 'admin')
  if (!isTauri || !isAdmin) return <>{children}</>
  if (localStorage.getItem('bilinote-onboarded') !== '1') return <Navigate to="/onboarding" replace />
  return <>{children}</>
}
const Model = lazy(() => import('@/pages/SettingPage/Model.tsx'))
const ProviderForm = lazy(() => import('@/components/Form/modelForm/Form.tsx'))
const AboutPage = lazy(() => import('@/pages/SettingPage/about.tsx'))
const Monitor = lazy(() => import('@/pages/SettingPage/Monitor.tsx'))
const Downloader = lazy(() => import('@/pages/SettingPage/Downloader.tsx'))
const DownloaderForm = lazy(() => import('@/components/Form/DownloaderForm/Form.tsx'))
const TranscriberPage = lazy(() => import('@/pages/SettingPage/transcriber.tsx'))
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage'))
const UsersPage = lazy(() => import('@/pages/SettingPage/Users'))

function AdminGuard({ children }: { children: React.ReactNode }) {
  const isAdmin = useAuthStore(state => state.user?.role === 'admin')
  return isAdmin ? <>{children}</> : <Navigate to="/" replace />
}

function AuthenticatedApp() {
  useTaskPolling(3000) // 每 3 秒轮询一次
  const isAdmin = useAuthStore(state => state.user?.role === 'admin')
  const user = useAuthStore(state => state.user)
  const loadTasks = useTaskStore(state => state.loadTasks)
  const [dataReady, setDataReady] = useState(false)
  const [dataError, setDataError] = useState('')
  const [retryData, setRetryData] = useState(0)

  useEffect(() => {
    if (!user) return
    let cancelled = false
    setDataError('')
    void (async () => {
      await importLegacyBrowserData(user)
      if (cancelled) return
      await loadTasks()
      if (!cancelled) setDataReady(true)
    })().catch(error => { if (!cancelled) setDataError(error?.msg || '加载历史失败，请重试') })
    return () => { cancelled = true }
  }, [user, loadTasks, retryData])

  // 在后端初始化完成后执行系统检查
  useEffect(() => {
    if (isAdmin) {
      systemCheck().catch(() => {})
    }
  }, [isAdmin])

  if (!dataReady) return <div className="flex h-screen flex-col items-center justify-center gap-4">
    <p>{dataError || '正在加载你的笔记…'}</p>
    {dataError && <Button onClick={() => setRetryData(value => value + 1)}>重试</Button>}
    <Button variant="ghost" onClick={() => { void useAuthStore.getState().logout().catch(() => {}) }}>退出登录</Button>
  </div>

  // 桌面端使用 HashRouter 避免刷新 404；Web 端继续使用 BrowserRouter
  const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  const Router = isTauri ? HashRouter : BrowserRouter

  // 后端已初始化，渲染主应用
  return (
    <>
      {isAdmin && <><StartupBanner /><BackendHealthIndicator /></>}
      <Router>
        <Suspense fallback={<div className="flex h-screen items-center justify-center">加载中…</div>}>
          <Routes>
            <Route path="/onboarding" element={<AdminGuard><Onboarding /></AdminGuard>} />
            <Route path="/" element={<OnboardingGuard><Index /></OnboardingGuard>}>
              <Route index element={<HomePage />} />
              <Route path="settings" element={<AdminGuard><SettingPage /></AdminGuard>}>
                <Route index element={<Navigate to="model" replace />} />
                <Route path="model" element={<Model />}>
                  <Route path="new" element={<ProviderForm isCreate />} />
                  <Route path=":id" element={<ProviderForm />} />
                </Route>
                <Route path="download" element={<Downloader />}>
                  <Route path=":id" element={<DownloaderForm />} />
                </Route>
                <Route path="transcriber" element={<TranscriberPage />} />
                <Route path="monitor" element={<Monitor />}></Route>
                <Route path="about" element={<AboutPage />}></Route>
                <Route path="users" element={<UsersPage />} />
                <Route path="*" element={<NotFoundPage />} />
              </Route>
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </Suspense>
      </Router>
    </>
  )
}

function App() {
  const { loading, initialized, failed, lastError, retry } = useCheckBackend()
  const { user, ready, bootstrap, expire } = useAuthStore()

  useEffect(() => {
    if (initialized) void bootstrap()
  }, [initialized, bootstrap])

  useEffect(() => {
    window.addEventListener('bilinote-auth-expired', expire)
    return () => window.removeEventListener('bilinote-auth-expired', expire)
  }, [expire])

  if (!initialized) {
    return <BackendInitDialog open={loading} failed={failed} lastError={lastError} onRetry={retry} />
  }
  if (!ready) return <div className="flex h-screen items-center justify-center">正在确认登录状态…</div>
  if (!user) return <LoginPage />
  return <AuthenticatedApp key={user.id} />
}

export default App
