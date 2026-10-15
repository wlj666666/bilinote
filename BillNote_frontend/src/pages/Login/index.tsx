import { useState, type FormEvent } from 'react'
import { useAuthStore } from '@/store/authStore'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import logo from '@/assets/icon.svg'

export default function LoginPage() {
  const login = useAuthStore(state => state.login)
  const register = useAuthStore(state => state.register)
  const [registering, setRegistering] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (registering && password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    setBusy(true)
    setError('')
    try {
      await (registering ? register : login)(username, password, remember)
      setPassword('')
      setConfirmPassword('')
    } catch (error: unknown) {
      setError((error as { msg?: string }).msg || (registering ? '注册失败，请稍后重试' : '登录失败，请稍后重试'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-linen px-6">
      <form key={registering ? 'register' : 'login'} onSubmit={submit} className="w-full max-w-sm space-y-5 rounded-2xl border border-border bg-paper p-8 shadow-sm">
        <div className="flex items-center gap-3">
          <img src={logo} alt="" className="h-11 w-11" />
          <div><h1 className="text-2xl font-semibold">BiliNote</h1><p className="text-sm text-muted-foreground">{registering ? '创建账号，开始整理视频笔记' : '登录后开始整理视频笔记'}</p></div>
        </div>
        <label className="block space-y-2"><span className="text-sm">账号</span>
          <Input name="username" value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" minLength={registering ? 3 : undefined} maxLength={64} pattern={registering ? '[A-Za-z0-9_.-]+' : undefined} required disabled={busy} />
          {registering && <p className="text-xs text-muted-foreground">3–64 位字母、数字、下划线、点或短横线</p>}
        </label>
        <label className="block space-y-2"><span className="text-sm">密码</span>
          <Input name="password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={registering ? 'new-password' : 'current-password'} minLength={registering ? 12 : undefined} maxLength={128} required disabled={busy} />
          {registering && <p className="text-xs text-muted-foreground">12–128 个字符</p>}
        </label>
        {registering && <label className="block space-y-2"><span className="text-sm">确认密码</span>
          <Input name="confirm-password" type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} autoComplete="new-password" minLength={12} maxLength={128} required disabled={busy} />
        </label>}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" name="remember-me" checked={remember} onChange={e => setRemember(e.target.checked)} disabled={busy} className="h-4 w-4 accent-sage" />
          <span>记住我，30 天内自动登录</span>
        </label>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <Button type="submit" className="w-full" disabled={busy}>{registering ? (busy ? '正在注册…' : '注册并登录') : (busy ? '正在登录…' : '登录')}</Button>
        <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={() => { setRegistering(!registering); setPassword(''); setConfirmPassword(''); setError('') }}>
          {registering ? '已有账号？去登录' : '没有账号？立即注册'}
        </Button>
        {registering && <p className="text-xs text-muted-foreground">暂不支持找回密码，请妥善保存，或使用浏览器保存账号密码。</p>}
      </form>
    </div>
  )
}
