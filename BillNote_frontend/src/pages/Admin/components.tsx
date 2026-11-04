import type { ReactNode } from 'react'
import { phaseLabels } from './format'
import { AlertCircle, ArrowLeft, ArrowRight, Inbox, Loader2, RefreshCw } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export function StatusBadge({ state, phase }: { state: string; phase?: string }) {
  const tone = ['SUCCESS', 'completed'].includes(state)
    ? 'success'
    : ['FAILED', 'failed'].includes(state)
      ? 'danger'
      : ['RUNNING', 'running'].includes(state)
        ? 'active'
        : ['QUEUED', 'queued'].includes(state)
          ? 'waiting'
          : 'neutral'
  return (
    <span className={`admin-badge ${tone}`}>
      <span />
      {phaseLabels[phase || state] || phase || state}
    </span>
  )
}

export function RefreshButton({ loading, onClick }: { loading: boolean; onClick: () => void }) {
  return (
    <button className="admin-button secondary" onClick={onClick} disabled={loading}>
      <RefreshCw size={15} className={loading ? 'admin-spin' : ''} />
      刷新
    </button>
  )
}

export function QueryError({ error }: { error: string }) {
  return error ? (
    <div className="admin-error" role="alert">
      <AlertCircle size={17} />
      <span>{error}。数据可能已过期，请刷新重试。</span>
    </div>
  ) : null
}

export function EmptyState({
  loading,
  title = '暂无记录',
  detail = '符合条件的记录将在这里展示。',
}: {
  loading?: boolean
  title?: string
  detail?: string
}) {
  return (
    <div className="admin-empty">
      {loading ? <Loader2 className="admin-spin" /> : <Inbox />}
      <strong>{loading ? '正在读取数据…' : title}</strong>
      <p>{loading ? '正在同步后端最新状态' : detail}</p>
    </div>
  )
}

export function Pagination({
  page,
  total,
  pageSize = 20,
  onChange,
}: {
  page: number
  total: number
  pageSize?: number
  onChange: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="admin-pagination">
      <span>
        共 <strong>{total}</strong> 条记录
      </span>
      <div>
        <button aria-label="上一页" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          <ArrowLeft size={15} />
        </button>
        <span>
          {page} / {pages}
        </span>
        <button aria-label="下一页" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          <ArrowRight size={15} />
        </button>
      </div>
    </div>
  )
}

export function AdminDialog({
  title,
  description,
  open,
  onClose,
  children,
  wide = false,
}: {
  title: string
  description: string
  open: boolean
  onClose: () => void
  children: ReactNode
  wide?: boolean
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={value => {
        if (!value) onClose()
      }}
    >
      <DialogContent className={`admin-dialog ${wide ? 'admin-dialog-wide' : ''}`}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  )
}
