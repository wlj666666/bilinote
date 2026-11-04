import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  Check,
  CheckCircle2,
  Cpu,
  Database,
  FileVideo,
  HardDrive,
  MemoryStick,
  MessageSquare,
  Server,
  ShieldCheck,
  Users,
  Zap,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { getAdminOverview, getAdminResources, type ResourceMetric } from '@/services/admin'
import { EmptyState, QueryError, RefreshButton } from './components'
import { bytes, formatTime } from './format'
import { useAdminQuery } from './useAdminQuery'

function Metric({
  title,
  value,
  icon: Icon,
  detail,
  tone = '',
}: {
  title: string
  value: number | undefined
  icon: LucideIcon
  detail: string
  tone?: string
}) {
  return (
    <div className={`admin-metric ${tone}`}>
      <div>
        <span>{title}</span>
        <Icon size={18} />
      </div>
      <strong>{value == null ? '—' : value.toLocaleString('zh-CN')}</strong>
      <small>{detail}</small>
    </div>
  )
}

function ResourceCard({
  title,
  icon: Icon,
  metric,
  stale,
  detail,
  value,
}: {
  title: string
  icon: LucideIcon
  metric?: ResourceMetric
  stale: boolean
  detail?: string
  value?: string
}) {
  const available = metric?.status === 'ok' && !stale
  const percent = available ? metric.percent : null
  const scope =
    metric?.scope === 'container' ? '容器' : metric?.scope === 'system' ? '系统可见范围' : ''
  return (
    <div className="admin-resource-card">
      <div className="admin-resource-label">
        <Icon size={17} />
        <span>{title}</span>
        <span className={`admin-dot ${available ? 'green' : 'muted'}`} />
      </div>
      <div className="admin-resource-value">
        {available ? value || (percent == null ? '未知' : `${percent}%`) : '—'}
      </div>
      <div
        className="admin-resource-track"
        role="meter"
        aria-label={title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-valuetext={percent == null ? '未采集' : `${percent}%`}
      >
        <span
          style={{ width: `${Math.min(100, Math.max(0, percent ?? 0))}%` }}
          className={percent != null && percent >= 90 ? 'danger' : ''}
        />
      </div>
      <small>
        {stale
          ? '连接中断，等待重新采集'
          : available
            ? detail || scope
            : metric?.reason || '正在读取指标…'}
      </small>
    </div>
  )
}

export default function AdminOverview() {
  const overview = useAdminQuery(getAdminOverview)
  const resources = useAdminQuery(getAdminResources)
  const data = overview.data
  const states = data?.note_states || {}
  const rs = resources.data?.resources
  const sv = resources.data?.services
  const serviceOK =
    !resources.error &&
    sv &&
    [sv.backend, sv.scheduler, sv.inference].every(service => service.status === 'ok')
  const services = sv
    ? [
        { title: '后端服务', icon: Server, value: sv.backend },
        { title: '任务调度器', icon: Activity, value: sv.scheduler },
        { title: '本地推理服务', icon: Zap, value: sv.inference },
      ]
    : []
  const gpu = rs?.gpu.devices?.[0]
  const gpuMetric: ResourceMetric | undefined = rs
    ? { ...rs.gpu, percent: gpu?.percent }
    : undefined
  const queue = states.QUEUED || 0
  const running = states.RUNNING || 0
  const chatRunning = data?.chat_states.running || 0
  const chatQueued = data?.chat_states.queued || 0
  const total = data?.note_total || 0
  const segments = [
    { key: 'SUCCESS', label: '已完成', color: '#718a70' },
    { key: 'RUNNING', label: '处理中', color: '#8babc0' },
    { key: 'QUEUED', label: '排队中', color: '#c8aa72' },
    { key: 'FAILED', label: '失败', color: '#c08070' },
    { key: 'CANCELLED', label: '已取消', color: '#b0aba1' },
    { key: 'UNKNOWN', label: '未知', color: '#d8d4cb' },
  ]
  return (
    <>
      <div className="admin-page-heading">
        <div>
          <div className="admin-eyebrow">OVERVIEW</div>
          <h1>
            系统总览<span className="admin-heading-dot">.</span>
          </h1>
          <p>从任务到资源，了解此刻的运行情况。</p>
        </div>
        <div className="admin-heading-actions">
          <span className="admin-updated">
            {data ? `${formatTime(data.observed_at)} 更新 · UTC+8` : '正在同步数据'}
          </span>
          <RefreshButton
            loading={overview.loading || resources.loading}
            onClick={() => {
              overview.refresh()
              resources.refresh()
            }}
          />
        </div>
      </div>
      <QueryError error={overview.error || resources.error} />
      <div className={`admin-health-strip ${serviceOK ? '' : 'attention'}`}>
        <span className="admin-health-icon">
          {serviceOK ? <ShieldCheck size={22} /> : <Activity size={22} />}
        </span>
        <div>
          <strong>
            {resources.error
              ? '暂时无法获取服务状态'
              : !sv
                ? '正在检查服务状态'
                : serviceOK
                  ? '核心服务运行正常'
                  : '部分服务需要关注'}
          </strong>
          <p>
            {resources.error
              ? '显示的数据可能已过期，请检查连接后刷新。'
              : '后端响应、任务调度与本地推理进程的当前状态。'}
          </p>
        </div>
        <span className="admin-live-label">
          <span className={`admin-dot ${serviceOK ? 'green' : 'muted'}`} />每 10 秒更新
        </span>
      </div>
      <div className="admin-metrics-grid">
        <Metric
          title="视频任务总数"
          value={data?.note_total}
          icon={FileVideo}
          detail="当前未删除的任务"
        />
        <Metric
          title="已完成任务"
          value={data ? states.SUCCESS || 0 : undefined}
          icon={CheckCircle2}
          detail="按最新任务状态统计"
          tone="sage"
        />
        <Metric
          title="失败任务"
          value={data ? states.FAILED || 0 : undefined}
          icon={ArrowDownRight}
          detail="可进入任务中心处理"
          tone="peach"
        />
        <Metric
          title="用户总数"
          value={data?.user_count}
          icon={Users}
          detail="包含管理员与停用账号"
        />
      </div>
      <div className="admin-overview-columns">
        <section className="admin-panel">
          <div className="admin-panel-heading">
            <div>
              <h2>任务运行情况</h2>
              <p>当前状态，一眼掌握</p>
            </div>
            <Link to="/admin/tasks" className="admin-text-link">
              查看任务
              <ArrowRight size={14} />
            </Link>
          </div>
          <div className="admin-queue-grid">
            <Link to="/admin/tasks?state=RUNNING">
              <span className="admin-queue-icon blue">
                <Activity size={19} />
              </span>
              <div>
                <strong>{data ? running : '—'}</strong>
                <small>正在执行</small>
              </div>
            </Link>
            <Link to="/admin/tasks?state=QUEUED">
              <span className="admin-queue-icon sand">
                <FileVideo size={19} />
              </span>
              <div>
                <strong>{data ? queue : '—'}</strong>
                <small>等待处理</small>
              </div>
            </Link>
          </div>
          <div className="admin-distribution">
            {total ? (
              segments.map(segment => (
                <span
                  key={segment.key}
                  title={`${segment.label} ${states[segment.key] || 0}`}
                  style={{
                    width: `${((states[segment.key] || 0) / total) * 100}%`,
                    background: segment.color,
                  }}
                />
              ))
            ) : (
              <span className="admin-distribution-empty" />
            )}
          </div>
          <div className="admin-distribution-legend">
            {segments.map(segment => (
              <span key={segment.key}>
                <i style={{ background: segment.color }} />
                {segment.label}
                <strong>{data ? states[segment.key] || 0 : '—'}</strong>
              </span>
            ))}
          </div>
          <Link to="/admin/tasks?kind=chat" className="admin-chat-summary">
            <MessageSquare size={18} />
            <div>
              <strong>AI 问答</strong>
              <span>当前保留的问答任务</span>
            </div>
            <span>
              {data ? chatRunning : '—'} 执行 · {data ? chatQueued : '—'} 排队
            </span>
            <ArrowRight size={15} />
          </Link>
        </section>
        <section className="admin-panel">
          <div className="admin-panel-heading">
            <div>
              <h2>服务状态</h2>
              <p>后端实际运行状态</p>
            </div>
            <span className="admin-mini-icon">
              <Server size={17} />
            </span>
          </div>
          {!sv && (
            <EmptyState
              loading={resources.loading}
              title="暂时无法读取"
              detail="请检查后端连接后刷新。"
            />
          )}
          <div className="admin-services">
            {services.map(({ title, icon: Icon, value }) => {
              const ok = value.status === 'ok' && !resources.error
              return (
                <div key={title}>
                  <span className="admin-service-icon">
                    <Icon size={18} />
                  </span>
                  <div>
                    <strong>{title}</strong>
                    <small>{resources.error ? '状态已过期' : value.detail}</small>
                  </div>
                  <span className={`admin-badge ${ok ? 'success' : 'danger'}`}>
                    <span />
                    {resources.error ? '未知' : ok ? '运行中' : '异常'}
                  </span>
                </div>
              )
            })}
          </div>
          <div className="admin-panel-note">
            <Database size={14} />
            视频任务统计与任务列表使用同一数据口径。
          </div>
        </section>
      </div>
      <section className="admin-panel admin-resources-panel">
        <div className="admin-panel-heading">
          <div>
            <h2>资源概况</h2>
            <p>
              当前采样值 · {rs ? formatTime(rs.observed_at) : '等待采集'}
              {rs?.environment === 'container' ? ' · 容器部署' : ''}
            </p>
          </div>
          <span className="admin-subtle-label">实时资源</span>
        </div>
        <div className="admin-resources-grid">
          <ResourceCard
            title="CPU"
            icon={Cpu}
            metric={rs?.cpu}
            stale={!!resources.error}
            detail={
              rs?.cpu.status === 'ok'
                ? `${rs.cpu.cores ?? '未知'} 核 · ${rs.cpu.scope === 'container' ? '容器配额' : '系统可见范围'}`
                : undefined
            }
          />
          <ResourceCard
            title="内存"
            icon={MemoryStick}
            metric={rs?.memory}
            stale={!!resources.error}
            detail={
              rs?.memory.status === 'ok'
                ? `${bytes(rs.memory.used_bytes)} / ${bytes(rs.memory.total_bytes)} · ${rs.memory.scope === 'container' ? '容器' : '系统可见范围'}`
                : undefined
            }
          />
          <ResourceCard
            title={rs?.disk.label || '磁盘'}
            icon={HardDrive}
            metric={rs?.disk}
            stale={!!resources.error}
            detail={
              rs?.disk.status === 'ok'
                ? `剩余 ${bytes(rs.disk.free_bytes)} / ${bytes(rs.disk.total_bytes)}`
                : undefined
            }
          />
          <ResourceCard
            title="GPU"
            icon={Zap}
            metric={gpuMetric}
            stale={!!resources.error}
            detail={
              gpu
                ? `${gpu.name} · 显存 ${bytes(gpu.used_bytes)} / ${bytes(gpu.total_bytes)}`
                : undefined
            }
          />
        </div>
        {(rs?.gpu.devices?.length || 0) > 1 &&
          rs?.gpu.devices?.slice(1).map(device => (
            <div className="admin-extra-gpu" key={device.name}>
              {device.name} · 利用率 {device.percent == null ? '未知' : `${device.percent}%`} · 显存{' '}
              {bytes(device.used_bytes)} / {bytes(device.total_bytes)}
            </div>
          ))}
      </section>
      <section className="admin-panel">
        <div className="admin-panel-heading">
          <div>
            <h2>最近失败任务</h2>
            <p>查看原因，及时处理</p>
          </div>
          <Link to="/admin/tasks?state=FAILED" className="admin-text-link">
            查看全部
            <ArrowRight size={14} />
          </Link>
        </div>
        {!data ? (
          <EmptyState loading={overview.loading} title="暂时无法读取" />
        ) : !data.recent_failed_notes.length && !data.recent_failed_chats.length ? (
          <div className="admin-no-errors">
            <span>
              <Check size={19} />
            </span>
            <div>
              <strong>当前没有失败记录</strong>
              <p>后续出现的失败任务将在这里展示。</p>
            </div>
          </div>
        ) : (
          <div className="admin-recent-list">
            {[...data.recent_failed_notes, ...data.recent_failed_chats].map(task => (
              <Link
                key={`${task.kind}-${task.id}`}
                to={`/admin/tasks?kind=${task.kind}&state=${task.kind === 'note' ? 'FAILED' : 'failed'}&q=${encodeURIComponent(task.id)}&detail=${encodeURIComponent(task.id)}`}
              >
                <span className="admin-task-icon">
                  {task.kind === 'note' ? <FileVideo size={18} /> : <MessageSquare size={18} />}
                </span>
                <div>
                  <strong>{task.title || '尚未获取视频标题'}</strong>
                  <small>
                    {task.username || '未知用户'} · {task.kind === 'note' ? '视频笔记' : 'AI 问答'}{' '}
                    · {task.id.slice(0, 8)}
                  </small>
                </div>
                <span className="admin-recent-time">
                  {formatTime(task.finished_at || task.created_at)}
                </span>
                <ArrowRight size={16} />
              </Link>
            ))}
          </div>
        )}
      </section>
      <p className="admin-footer-note">
        视频统计为当前未删除任务的最新状态；问答统计为当前保留记录。资源不可读取时显示未知。
      </p>
    </>
  )
}
