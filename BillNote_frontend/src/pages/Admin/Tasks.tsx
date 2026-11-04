import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { FileVideo, Filter, MessageSquare, RotateCcw, Search, UserRound, X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import {
  cancelAdminTask,
  getAdminTask,
  getAdminTasks,
  retryAdminTask,
  type AdminTask,
} from '@/services/admin'
import {
  AdminDialog,
  EmptyState,
  Pagination,
  QueryError,
  RefreshButton,
  StatusBadge,
} from './components'
import { duration, formatTime, phaseLabels } from './format'
import { adminError, useAdminQuery } from './useAdminQuery'

function TaskDetail({
  kind,
  id,
  onClose,
}: {
  kind: 'note' | 'chat'
  id: string
  onClose: () => void
}) {
  const fetcher = useCallback((signal: AbortSignal) => getAdminTask(kind, id, signal), [kind, id])
  const { data, error, loading, refresh } = useAdminQuery(fetcher)
  return (
    <AdminDialog
      title="任务详情"
      description={kind === 'note' ? '视频笔记的当前处理信息' : 'AI 问答任务的运行信息'}
      open
      onClose={onClose}
      wide
    >
      <QueryError error={error} />
      {!data ? (
        <EmptyState loading={loading} title="未能读取任务" />
      ) : (
        <>
          <div className="admin-detail-title">
            <span className="admin-task-icon">
              {kind === 'note' ? <FileVideo size={22} /> : <MessageSquare size={22} />}
            </span>
            <div>
              <h3>{data.title || '尚未获取视频标题'}</h3>
              <code>{data.id}</code>
            </div>
            <StatusBadge state={data.state} phase={data.phase} />
          </div>
          <dl className="admin-detail-grid">
            <div>
              <dt>所属用户</dt>
              <dd>
                {data.username || '未知'}
                {data.user_id != null ? ` #${data.user_id}` : ''}
                {data.user_enabled === false && ' · 已停用'}
              </dd>
            </div>
            <div>
              <dt>来源平台</dt>
              <dd>{data.platform || '未知'}</dd>
            </div>
            <div>
              <dt>使用模型</dt>
              <dd>{data.model_name || '未知'}</dd>
            </div>
            <div>
              <dt>当前阶段</dt>
              <dd>{phaseLabels[data.phase] || data.phase}</dd>
            </div>
            <div>
              <dt>提交时间 · UTC+8</dt>
              <dd>{formatTime(data.created_at)}</dd>
            </div>
            <div>
              <dt>结束时间 · UTC+8</dt>
              <dd>{data.finished_at == null ? '尚无记录' : formatTime(data.finished_at)}</dd>
            </div>
            <div>
              <dt>总耗时（含排队）</dt>
              <dd>{duration(data.elapsed_seconds)}</dd>
            </div>
            <div>
              <dt>{kind === 'note' ? '最近心跳 · UTC+8' : '关联视频任务'}</dt>
              <dd>
                {kind === 'note' ? formatTime(data.heartbeat_at) : <code>{data.task_id}</code>}
              </dd>
            </div>
          </dl>
          {data.message && <div className="admin-detail-message">{data.message}</div>}
          {data.error_detail && (
            <div className="admin-detail-error">
              <strong>{data.error_type || '失败原因'}</strong>
              <pre>{data.error_detail}</pre>
            </div>
          )}
          {!!Object.keys(data.stage_times || {}).length && (
            <div className="admin-stage-list">
              <h4>已记录的阶段累计耗时</h4>
              {Object.entries(data.stage_times || {}).map(([stage, seconds]) => (
                <div key={stage}>
                  <span>{phaseLabels[stage] || stage}</span>
                  <strong>{duration(seconds)}</strong>
                </div>
              ))}
            </div>
          )}
          <div className="admin-dialog-footer">
            <span>运行信息每 10 秒同步</span>
            <RefreshButton loading={loading} onClick={refresh} />
          </div>
        </>
      )}
    </AdminDialog>
  )
}

export default function AdminTasks() {
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind') === 'chat' ? 'chat' : 'note'
  const state = params.get('state') || ''
  const query = params.get('q') || ''
  const requestedPage = Number(params.get('page'))
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? requestedPage : 1
  const requestedUser = Number(params.get('user_id'))
  const userId =
    Number.isSafeInteger(requestedUser) && requestedUser > 0 ? requestedUser : undefined
  const detailId = params.get('detail')
  const [search, setSearch] = useState(query)
  const [userSearch, setUserSearch] = useState(userId?.toString() || '')
  const [action, setAction] = useState<{ type: 'cancel' | 'retry'; task: AdminTask } | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const retryIds = useRef(new Map<string, string>())
  useEffect(() => {
    setSearch(query)
    setUserSearch(userId?.toString() || '')
  }, [query, userId])
  const fetcher = useCallback(
    (signal: AbortSignal) =>
      getAdminTasks(
        {
          kind,
          page,
          state: state || undefined,
          user_id: userId,
          q: query || undefined,
        },
        signal
      ),
    [kind, page, state, userId, query]
  )
  const { data, error, loading, refresh } = useAdminQuery(fetcher)

  function change(values: Record<string, string | undefined>) {
    setParams(current => {
      const next = new URLSearchParams(current)
      for (const [key, value] of Object.entries(values)) {
        if (value) next.set(key, value)
        else next.delete(key)
      }
      return next
    })
  }
  function applySearch(event: FormEvent) {
    event.preventDefault()
    change({
      q: search.trim() || undefined,
      user_id: userSearch || undefined,
      page: undefined,
      detail: undefined,
    })
  }
  async function execute() {
    if (!action || busy) return
    setBusy(true)
    setActionError('')
    try {
      if (action.type === 'cancel') await cancelAdminTask(action.task.id)
      else {
        const key = `${action.task.id}:${action.task.updated_at}`
        const requestId = retryIds.current.get(key) || crypto.randomUUID()
        retryIds.current.set(key, requestId)
        await retryAdminTask(action.task, requestId)
      }
      toast.success(action.type === 'cancel' ? '排队任务已取消' : '任务已按原用户重新排队')
      setAction(null)
      refresh()
    } catch (e) {
      setActionError(adminError(e))
      refresh()
    } finally {
      setBusy(false)
    }
  }
  const statuses =
    kind === 'note'
      ? [
          'QUEUED',
          'RUNNING',
          'WAITING_RESOURCE',
          'WAITING_RETRY',
          'SUCCESS',
          'FAILED',
          'CANCELLED',
          'UNKNOWN',
        ]
      : ['queued', 'running', 'completed', 'failed']
  return (
    <>
      <div className="admin-page-heading">
        <div>
          <div className="admin-eyebrow">TASK MONITOR</div>
          <h1>
            任务监控<span className="admin-heading-dot">.</span>
          </h1>
          <p>查看每一项任务，找到需要关注的环节。</p>
        </div>
        <div className="admin-heading-actions">
          <span className="admin-updated">
            每 10 秒同步{data ? ` · ${formatTime(data.observed_at)}` : ''}
          </span>
          <RefreshButton loading={loading} onClick={refresh} />
        </div>
      </div>
      <QueryError error={error} />
      <section className="admin-panel admin-task-panel">
        <div className="admin-task-tabs" role="tablist" aria-label="任务类型">
          <button
            role="tab"
            aria-selected={kind === 'note'}
            className={kind === 'note' ? 'active' : ''}
            onClick={() =>
              change({ kind: undefined, state: undefined, page: undefined, detail: undefined })
            }
          >
            <FileVideo size={17} />
            视频笔记
          </button>
          <button
            role="tab"
            aria-selected={kind === 'chat'}
            className={kind === 'chat' ? 'active' : ''}
            onClick={() =>
              change({ kind: 'chat', state: undefined, page: undefined, detail: undefined })
            }
          >
            <MessageSquare size={17} />
            AI 问答
          </button>
          <span>{kind === 'note' ? '当前未删除的任务' : '当前保留的网页问答任务'}</span>
        </div>
        <form className="admin-filters" onSubmit={applySearch}>
          <label className="admin-search">
            <Search size={17} />
            <input
              aria-label="搜索任务编号"
              placeholder="输入任务编号搜索…"
              value={search}
              maxLength={128}
              onChange={event => setSearch(event.target.value)}
            />
          </label>
          <label className="admin-user-filter">
            <UserRound size={16} />
            <input
              aria-label="按用户 ID 筛选"
              type="number"
              min={1}
              step={1}
              placeholder="用户 ID"
              value={userSearch}
              onChange={event => setUserSearch(event.target.value)}
            />
          </label>
          <label className="admin-state-filter">
            <Filter size={15} />
            <select
              aria-label="筛选任务状态"
              value={state}
              onChange={event =>
                change({ state: event.target.value || undefined, page: undefined })
              }
            >
              <option value="">全部状态</option>
              {statuses.map(value => (
                <option key={value} value={value}>
                  {phaseLabels[value]}
                </option>
              ))}
            </select>
          </label>
          <button className="admin-button secondary" type="submit">
            查询
          </button>
          {(query || state || userId) && (
            <button
              className="admin-reset"
              type="button"
              onClick={() => {
                setSearch('')
                setUserSearch('')
                change({
                  q: undefined,
                  state: undefined,
                  user_id: undefined,
                  page: undefined,
                  detail: undefined,
                })
              }}
            >
              <X size={14} />
              重置
            </button>
          )}
        </form>
        {!data?.items.length ? (
          <EmptyState
            loading={loading && !data}
            title={error ? '暂时无法读取任务' : '没有符合条件的任务'}
            detail="可以调整筛选条件，或等待用户提交新任务。"
          />
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>任务 / 视频</th>
                  <th>所属用户</th>
                  <th>当前状态</th>
                  <th>提交时间 · UTC+8</th>
                  <th>总耗时</th>
                  <th className="admin-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map(task => (
                  <tr key={task.id}>
                    <td>
                      <button
                        className="admin-task-title"
                        onClick={() => change({ detail: task.id })}
                      >
                        <span className="admin-task-icon">
                          {kind === 'note' ? <FileVideo size={18} /> : <MessageSquare size={18} />}
                        </span>
                        <span>
                          <strong>{task.title || '尚未获取视频标题'}</strong>
                          <small>
                            {task.platform} · {task.id.slice(0, 8)}
                            <span className="admin-model-text">
                              {' '}
                              · {task.model_name || '模型未知'}
                            </span>
                          </small>
                        </span>
                      </button>
                    </td>
                    <td>
                      <span className="admin-user-name">{task.username || '未知用户'}</span>
                      <small className="admin-cell-sub">
                        {task.user_id == null ? '归属未知' : `#${task.user_id}`}
                        {task.user_enabled === false ? ' · 已停用' : ''}
                      </small>
                    </td>
                    <td>
                      <StatusBadge state={task.state} phase={task.phase} />
                    </td>
                    <td className="admin-nowrap">{formatTime(task.created_at)}</td>
                    <td className="admin-nowrap">{duration(task.elapsed_seconds)}</td>
                    <td>
                      <div className="admin-row-actions">
                        <button onClick={() => change({ detail: task.id })}>详情</button>
                        {task.can_cancel && (
                          <button
                            disabled={busy}
                            onClick={() => {
                              setActionError('')
                              setAction({ type: 'cancel', task })
                            }}
                          >
                            取消排队
                          </button>
                        )}
                        {task.can_retry && (
                          <button
                            disabled={busy}
                            onClick={() => {
                              setActionError('')
                              setAction({ type: 'retry', task })
                            }}
                          >
                            <RotateCcw size={13} />
                            重试
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pagination
          page={page}
          total={data?.total || 0}
          onChange={value =>
            change({ page: value === 1 ? undefined : String(value), detail: undefined })
          }
        />
      </section>
      <p className="admin-footer-note">
        任务详情仅展示运行信息。未知耗时不作推测；问答记录沿用后端现有保留策略。
      </p>
      {detailId && (
        <TaskDetail
          key={`${kind}-${detailId}`}
          kind={kind}
          id={detailId}
          onClose={() => change({ detail: undefined })}
        />
      )}
      {action && (
        <AdminDialog
          title={action.type === 'cancel' ? '取消这个排队任务？' : '重新处理这个失败任务？'}
          description={`${action.task.username || '未知用户'} · ${action.task.title || action.task.id}`}
          open
          onClose={() => {
            if (!busy) setAction(null)
          }}
        >
          <p className="admin-confirm-text">
            {action.type === 'cancel'
              ? '任务尚未执行，取消后可由原用户重新提交。如果任务已经开始，取消请求将被拒绝。'
              : '将保留原用户归属，使用原任务参数和可复用缓存重新排队。任务需要通过账号、模型与队列检查。'}
          </p>
          {actionError && (
            <p className="admin-inline-error" role="alert">
              {actionError}
            </p>
          )}
          <div className="admin-dialog-footer">
            <button
              className="admin-button secondary"
              disabled={busy}
              onClick={() => setAction(null)}
            >
              返回
            </button>
            <button
              className="admin-button"
              disabled={busy}
              onClick={() => {
                void execute()
              }}
            >
              {busy ? '处理中…' : '确认操作'}
            </button>
          </div>
        </AdminDialog>
      )}
    </>
  )
}
