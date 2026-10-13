import { useEffect, useState, type FormEvent } from 'react'
import request from '@/utils/request'
import type { AuthUser } from '@/store/authStore'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import toast from 'react-hot-toast'

export default function UsersPage() {
  const [users, setUsers] = useState<AuthUser[]>([])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [resetUser, setResetUser] = useState<AuthUser | null>(null)
  const [newPassword, setNewPassword] = useState('')

  const reload = async () => {
    setUsers(await request.get('/auth/users') as unknown as AuthUser[])
  }
  useEffect(() => { void reload().catch(() => {}) }, [])

  const create = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      await request.post('/auth/users', { username, password })
      setUsername('')
      setPassword('')
      await reload()
      toast.success('客户账号已创建')
    } catch { /* request interceptor shows errors */ }
    finally { setBusy(false) }
  }

  const toggle = async (user: AuthUser) => {
    setBusy(true)
    try {
      await request.patch(`/auth/users/${user.id}`, { enabled: !user.enabled })
      await reload()
      toast.success(user.enabled ? '账号已停用，登录会话已撤销' : '账号已启用')
    } catch { /* request interceptor shows errors */ }
    finally { setBusy(false) }
  }

  const reset = async (event: FormEvent) => {
    event.preventDefault()
    if (!resetUser) return
    setBusy(true)
    try {
      await request.patch(`/auth/users/${resetUser.id}`, { password: newPassword })
      setResetUser(null)
      setNewPassword('')
      toast.success('密码已重置，旧登录会话已撤销')
    } catch { /* request interceptor shows errors */ }
    finally { setBusy(false) }
  }

  return (
    <div className="h-full overflow-auto bg-paper p-8">
      <div className="mx-auto max-w-3xl space-y-6">
        <div><h1 className="text-2xl font-semibold">客户账号</h1><p className="mt-2 text-sm text-muted-foreground">创建和管理客户登录账号。客户只能使用业务功能，无法访问系统配置。</p></div>
        <form onSubmit={create} className="space-y-4 rounded-xl border border-border bg-linen p-5">
          <h2 className="font-medium">创建客户账号</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="space-y-2 text-sm"><span>账号</span><Input value={username} onChange={e => setUsername(e.target.value)} minLength={3} maxLength={64} pattern="[A-Za-z0-9_.-]+" placeholder="字母、数字、下划线、点或短横线" autoComplete="off" required disabled={busy} /></label>
            <label className="space-y-2 text-sm"><span>初始密码</span><Input type="password" value={password} onChange={e => setPassword(e.target.value)} minLength={12} maxLength={128} placeholder="至少 12 个字符" autoComplete="new-password" required disabled={busy} /></label>
          </div>
          <Button type="submit" disabled={busy}>{busy ? '处理中…' : '创建账号'}</Button>
        </form>
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-sm">
            <thead className="bg-linen"><tr><th className="p-3">账号</th><th className="p-3">角色</th><th className="p-3">状态</th><th className="p-3">操作</th></tr></thead>
            <tbody>{users.map(user => <tr key={user.id} className="border-t border-border">
              <td className="p-3">{user.username}</td><td className="p-3">{user.role === 'admin' ? '管理员' : '客户'}</td><td className="p-3">{user.enabled ? '已启用' : '已停用'}</td>
              <td className="p-3">{user.role === 'user' && <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={busy} onClick={() => { void toggle(user) }}>{user.enabled ? '停用' : '启用'}</Button>
                <Button size="sm" variant="outline" disabled={busy} onClick={() => { setNewPassword(''); setResetUser(user) }}>重置密码</Button>
              </div>}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </div>
      <Dialog open={Boolean(resetUser)} onOpenChange={open => { if (!open && !busy) { setResetUser(null); setNewPassword('') } }}>
        <DialogContent><DialogHeader><DialogTitle>重置 {resetUser?.username} 的密码</DialogTitle></DialogHeader>
          <form onSubmit={reset} className="space-y-4">
            <Input aria-label="新密码" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} minLength={12} maxLength={128} autoComplete="new-password" placeholder="新密码，至少 12 个字符" required disabled={busy} />
            <p className="text-sm text-muted-foreground">保存后，该账号需要使用新密码重新登录。</p>
            <Button type="submit" disabled={busy}>{busy ? '处理中…' : '保存新密码'}</Button>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
