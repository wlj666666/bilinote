import { Button } from '@/components/ui/button'
import { useQuotaStore } from '@/store/modelQuotaStore'

const date = (timestamp: number) => new Date(timestamp * 1000).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' })

export function ModelQuotaLabel({ providerId, modelName }: { providerId: string; modelName: string }) {
  const snapshot = useQuotaStore(state => state.snapshot)
  if (providerId !== 'qwen') return null
  const row = snapshot?.models.find(model => model.provider_id === providerId && model.model_name === modelName)
  if (!snapshot) return <span className="text-xs text-muted-foreground">额度查询中…</span>
  if (snapshot.status !== 'ok') return <span className="text-xs text-muted-foreground">{snapshot.status === 'needs_login' ? '额度待授权' : '额度暂不可查'}</span>
  if (!row || row.status === 'unknown' || row.status === 'unsupported') return <span className="text-xs text-muted-foreground">免费额度未知</span>
  const expired = row.status === 'expired'
  const exhausted = row.status === 'exhausted'
  return (
    <span className={`text-xs ${expired || exhausted ? 'text-amber-600' : 'text-muted-foreground'}`}>
      {expired ? '免费额度已到期' : exhausted ? '免费额度已耗尽' : `剩余 ${row.remaining_tokens?.toLocaleString('zh-CN')} Token`}
      {row.expires_at != null && ` · ${date(row.expires_at)} 到期`}
    </span>
  )
}

export function ModelQuotaStatus() {
  const { snapshot, loading, refresh } = useQuotaStore()
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
      <span>{snapshot?.message || '正在查询阿里云免费额度'}</span>
      {snapshot?.updated_at != null && <span>更新于 {new Date(snapshot.updated_at * 1000).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai' })} · 每 60 秒刷新</span>}
      <Button type="button" variant="ghost" size="sm" disabled={loading} onClick={() => { void refresh() }}>
        {loading ? '同步中…' : '刷新额度'}
      </Button>
    </div>
  )
}

export function ModelQuotaDetails({ providerId, modelName }: { providerId: string; modelName: string }) {
  const snapshot = useQuotaStore(state => state.snapshot)
  const row = snapshot?.models.find(model => model.provider_id === providerId && model.model_name === modelName)
  if (snapshot?.status !== 'ok' || !row || row.total_tokens == null || row.status === 'unknown' || row.status === 'unsupported') return null
  return <span className="text-xs text-muted-foreground">总免费额度 {row.total_tokens.toLocaleString('zh-CN')} Token</span>
}
