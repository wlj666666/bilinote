import { useState, type FormEvent } from 'react'
import { useAuthStore } from '@/store/authStore'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import logo from '@/assets/icon.svg'

export default function LoginPage() {
  const login = useAuthStore(state => state.login)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await login(username, password)
      setPassword('')
    } catch (error: unknown) {
      setError((error as { msg?: string }).msg || '登录失败，请稍后重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-linen px-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-2xl border border-border bg-paper p-8 shadow-sm">
        <div className="flex items-center gap-3">
          <img src={logo} alt="" className="h-11 w-11" />
          <div><h1 className="text-2xl font-semibold">BiliNote</h1><p className="text-sm text-muted-foreground">登录后开始整理视频笔记</p></div>
        </div>
        <label className="block space-y-2"><span className="text-sm">账号</span>
          <Input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" maxLength={64} required disabled={busy} />
        </label>
        <label className="block space-y-2"><span className="text-sm">密码</span>
          <Input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" maxLength={128} required disabled={busy} />
        </label>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <Button type="submit" className="w-full" disabled={busy}>{busy ? '正在登录…' : '登录'}</Button>
        <p className="text-xs text-muted-foreground">账号由管理员创建。如需开通账号或重置密码，请联系管理员。</p>
      </form>
    </div>
  )
}
