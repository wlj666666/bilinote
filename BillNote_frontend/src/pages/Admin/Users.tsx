import { useCallback, useState, type FormEvent } from 'react'
import { ArrowUpRight, KeyRound, Plus, Search, ShieldCheck, Users } from 'lucide-react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { createAdminUser, getAdminUsers, updateAdminUser, type AdminUser } from '@/services/admin'
import { AdminDialog, EmptyState, Pagination, QueryError, RefreshButton } from './components'
import { formatTime } from './format'
import { adminError, useAdminQuery } from './useAdminQuery'

export default function AdminUsers() {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [enabled, setEnabled] = useState('')
  const [page, setPage] = useState(1)
  const [dialog, setDialog] = useState<{
    type: 'create' | 'reset' | 'toggle'
    user?: AdminUser
  } | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const fetcher = useCallback(
    (signal: AbortSignal) =>
      getAdminUsers(
        {
          q: query || undefined,
          page,
          enabled: enabled ? enabled === 'true' : undefined,
        },
        signal
      ),
    [query, page, enabled]
  )
  const { data, error, loading, refresh } = useAdminQuery(fetcher)
  function open(type: 'create' | 'reset' | 'toggle', user?: AdminUser) {
    setUsername('')
    setPassword('')
    setActionError('')
    setDialog({ type, user })
  }
  function close() {
    if (!busy) {
      setDialog(null)
      setPassword('')
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!dialog || busy) return
    setBusy(true)
    setActionError('')
    try {
      if (dialog.type === 'create') await createAdminUser(username.trim(), password)
      else if (dialog.user)
        await updateAdminUser(
          dialog.user.id,
          dialog.type === 'reset' ? { password } : { enabled: !dialog.user.enabled }
        )
      toast.success(
        dialog.type === 'create'
          ? '用户已创建'
          : dialog.type === 'reset'
            ? '密码已重置，旧会话已撤销'
            : '账号状态已更新'
      )
      setDialog(null)
      setPassword('')
      refresh()
    } catch (e) {
      setActionError(adminError(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <div className="admin-page-heading">
        <div>
          <div className="admin-eyebrow">PEOPLE</div>
          <h1>
            用户管理<span className="admin-heading-dot">.</span>
          </h1>
          <p>管理使用权限，让每一位用户各得其所。</p>
        </div>
        <div className="admin-heading-actions">
          <RefreshButton loading={loading} onClick={refresh} />
          <button className="admin-button" onClick={() => open('create')}>
            <Plus size={17} />
            创建用户
          </button>
        </div>
      </div>
      <QueryError error={error} />
      <div className="admin-people-banner">
        <span className="admin-people-icon">
          <Users size={25} />
        </span>
        <div>
          <strong>账号与访问权限</strong>
          <p>普通用户可使用笔记与问答功能，系统配置仅对管理员开放。</p>
        </div>
        <span>
          <ShieldCheck size={16} />
          权限隔离
        </span>
      </div>
      <section className="admin-panel admin-task-panel">
        <form
          className="admin-filters"
          onSubmit={event => {
            event.preventDefault()
            setQuery(search.trim())
            setPage(1)
          }}
        >
          <label className="admin-search">
            <Search size={17} />
            <input
              aria-label="搜索账号"
              placeholder="搜索用户账号…"
              value={search}
              maxLength={64}
              onChange={event => setSearch(event.target.value)}
            />
          </label>
          <select
            className="admin-select"
            aria-label="筛选账号状态"
            value={enabled}
            onChange={event => {
              setEnabled(event.target.value)
              setPage(1)
            }}
          >
            <option value="">全部账号状态</option>
            <option value="true">已启用</option>
            <option value="false">已停用</option>
          </select>
          <button className="admin-button secondary" type="submit">
            查询
          </button>
          <span className="admin-filter-caption">
            {data ? `${formatTime(data.observed_at)} 更新` : '正在读取用户'}
          </span>
        </form>
        {!data?.items.length ? (
          <EmptyState
            loading={loading && !data}
            title={error ? '暂时无法读取用户' : '没有符合条件的用户'}
            detail="调整搜索条件，或创建一个新账号。"
          />
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>用户</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>注册 / 最近登录 · UTC+8</th>
                  <th>任务数</th>
                  <th className="admin-right">管理操作</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map(user => (
                  <tr key={user.id}>
                    <td>
                      <div className="admin-user-identity">
                        <span className="admin-user-avatar">
                          {user.username.slice(0, 1).toUpperCase()}
                        </span>
                        <span>
                          <strong>{user.username}</strong>
                          <small>用户 #{user.id}</small>
                        </span>
                      </div>
                    </td>
                    <td>
                      <span className={`admin-role ${user.role === 'admin' ? 'owner' : ''}`}>
                        {user.role === 'admin' && <ShieldCheck size={12} />}
                        {user.role === 'admin' ? '管理员' : '普通用户'}
                      </span>
                    </td>
                    <td>
                      <span className={`admin-badge ${user.enabled ? 'success' : 'neutral'}`}>
                        <span />
                        {user.enabled ? '已启用' : '已停用'}
                      </span>
                    </td>
                    <td>
                      <span className="admin-nowrap">{formatTime(user.created_at)}</span>
                      <small className="admin-cell-sub">
                        最近 {formatTime(user.last_login_at)}
                      </small>
                    </td>
                    <td>
                      <Link className="admin-task-count" to={`/admin/tasks?user_id=${user.id}`}>
                        {user.task_count}
                        <ArrowUpRight size={13} />
                      </Link>
                    </td>
                    <td>
                      <div className="admin-row-actions">
                        {user.role === 'user' ? (
                          <>
                            <button disabled={busy} onClick={() => open('reset', user)}>
                              <KeyRound size={13} />
                              重置密码
                            </button>
                            <button disabled={busy} onClick={() => open('toggle', user)}>
                              {user.enabled ? '停用' : '启用'}
                            </button>
                          </>
                        ) : (
                          <span className="admin-muted">服务器维护</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination page={page} total={data?.total || 0} onChange={setPage} />
      </section>
      <p className="admin-footer-note">
        任务数统计当前未删除的视频任务。停用账号不会删除数据；重置密码会撤销旧登录会话。
      </p>
      {dialog && (
        <AdminDialog
          title={
            dialog.type === 'create'
              ? '创建用户'
              : dialog.type === 'reset'
                ? '重置用户密码'
                : `${dialog.user?.enabled ? '停用' : '启用'}账号？`
          }
          description={
            dialog.type === 'create'
              ? '创建一个可使用笔记与问答的普通账号。'
              : `用户 ${dialog.user?.username}`
          }
          open
          onClose={close}
        >
          <form
            className="admin-user-form"
            onSubmit={event => {
              void submit(event)
            }}
          >
            {dialog.type === 'create' && (
              <label>
                账号
                <input
                  name="username"
                  required
                  minLength={3}
                  maxLength={64}
                  pattern="[A-Za-z0-9_.-]+"
                  autoComplete="off"
                  placeholder="3–64 位字母、数字、下划线、点或短横线"
                  value={username}
                  disabled={busy}
                  onChange={event => setUsername(event.target.value)}
                />
              </label>
            )}
            {dialog.type !== 'toggle' ? (
              <>
                <label>
                  {dialog.type === 'create' ? '初始密码' : '新密码'}
                  <input
                    name="password"
                    type="password"
                    autoComplete="new-password"
                    minLength={12}
                    maxLength={128}
                    required
                    placeholder="12–128 个字符"
                    value={password}
                    disabled={busy}
                    onChange={event => setPassword(event.target.value)}
                  />
                </label>
                <p>
                  请通过安全方式将密码告知用户。
                  {dialog.type === 'reset' && '保存后，该用户需要重新登录。'}
                </p>
              </>
            ) : (
              <p className="admin-confirm-text">
                {dialog.user?.enabled
                  ? '停用后，已有登录会话失效，新请求将被拒绝。正在运行的任务允许完成，排队任务停止领取，用户数据保留。'
                  : '启用后，用户可以重新登录并提交任务，原有排队任务可以继续被领取。'}
              </p>
            )}
            {actionError && (
              <p className="admin-inline-error" role="alert">
                {actionError}
              </p>
            )}
            <div className="admin-dialog-footer">
              <button
                type="button"
                className="admin-button secondary"
                disabled={busy}
                onClick={close}
              >
                取消
              </button>
              <button type="submit" className="admin-button" disabled={busy}>
                {busy ? '处理中…' : dialog.type === 'create' ? '创建账号' : '确认保存'}
              </button>
            </div>
          </form>
        </AdminDialog>
      )}
    </>
  )
}
