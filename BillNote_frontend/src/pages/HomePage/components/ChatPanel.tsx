import { useState, useEffect, useCallback, useMemo, useRef, useId } from 'react'
import { Bubble, Sender } from '@ant-design/x'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Loader2, Trash2, ChevronDown, ChevronUp, BookOpen, UserRound, Bot, Maximize2, Minimize2 } from 'lucide-react'
import { toast } from 'react-hot-toast'
import { useChatStore } from '@/store/chatStore'
import { useTaskStore } from '@/store/taskStore'
import { useModelStore } from '@/store/modelStore'
import { ModelQuotaLabel } from '@/components/ModelQuota'
import { useModelQuotas } from '@/store/modelQuotaStore'
import { askQuestion, getActiveChat, waitForAnswer, getChatStatus, indexTask, type ChatSource, type IndexStatus } from '@/services/chat'

type ChatMode = 'half' | 'full'

interface ChatPanelProps {
  taskId: string
  mode: ChatMode
  onModeChange: (mode: ChatMode) => void
}

function SourceBadges({ sources }: { sources: ChatSource[] }) {
  const [expanded, setExpanded] = useState(false)

  if (!sources || sources.length === 0) return null

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-neutral-400 hover:text-neutral-600"
      >
        <BookOpen className="h-3 w-3" />
        <span>引用来源 ({sources.length})</span>
        {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      </button>
      {expanded && (
        <div className="mt-1 flex flex-wrap gap-1">
          {sources.map((s, i) => (
            <Badge key={i} variant="outline" className="text-xs font-normal">
              {s.source_type === 'meta' ? '视频信息' : s.source_type === 'markdown'
                ? s.section_title || '笔记'
                : `${(s.start_time ?? 0).toFixed(0)}s ~ ${(s.end_time ?? 0).toFixed(0)}s`}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ChatPanel({ taskId, mode, onModeChange }: ChatPanelProps) {
  const { refresh: refreshQuotas } = useModelQuotas()
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState('正在生成回答…')
  const [indexAttempt, setIndexAttempt] = useState(0)
  const chatController = useRef<AbortController | null>(null)
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null)
  const modelSelectId = useId()

  const messages = useChatStore(state => state.chatHistory[taskId]) ?? []
  const addMessage = useChatStore(state => state.addMessage)
  const clearChat = useChatStore(state => state.clearChat)
  const loadHistory = useChatStore(state => state.loadHistory)
  const selectedModelId = useChatStore(state => state.modelSelections[taskId])
  const selectModel = useChatStore(state => state.selectModel)
  const modelList = useModelStore(state => state.modelList)
  const modelsLoading = useModelStore(state => state.loading)
  const loadEnabledModels = useModelStore(state => state.loadEnabledModels)

  const tasks = useTaskStore(state => state.tasks)
  const currentTask = useMemo(
    () => tasks.find(t => t.id === taskId) ?? null,
    [tasks, taskId],
  )
  const selectedModel = modelList.find(model => model.id === selectedModelId)
    ?? modelList.find(model => model.provider_id === currentTask?.formData?.provider_id
      && model.model_name === currentTask?.formData?.model_name)
    ?? modelList[0]

  useEffect(() => {
    void loadEnabledModels()
  }, [loadEnabledModels])

  // Resume a server-owned answer after navigation/reload. Abort only local polling.
  useEffect(() => {
    const controller = new AbortController()
    chatController.current = controller
    setLoading(true)
    setInput('')
    const resume = async () => {
      try {
        const job = await getActiveChat(taskId, controller.signal)
        if (controller.signal.aborted) return
        await loadHistory(taskId)
        if (controller.signal.aborted) return
        if (job) {
          await waitForAnswer(job, controller.signal, setProgress)
          if (!controller.signal.aborted) await loadHistory(taskId)
        }
      } catch (error: any) {
        if (!controller.signal.aborted) toast.error(error?.msg || error?.message || '无法读取问答状态，请稍后重试')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void resume()
    return () => controller.abort()
  }, [taskId, loadHistory])

  // 检查索引状态，未索引时自动触发，indexing 时轮询
  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    setIndexStatus(null)

    const poll = async () => {
      try {
        const res = await getChatStatus(taskId)
        if (cancelled) return
        setIndexStatus(res.status)

        if (res.status === 'idle') {
          // 未索引，触发后台索引
          await indexTask(taskId)
          if (!cancelled) setIndexStatus('indexing')
        }

        // indexing 状态持续轮询
        if (!cancelled && (res.status === 'indexing' || res.status === 'idle')) {
          timer = setTimeout(poll, 2000)
        }
      } catch {
        if (!cancelled) setIndexStatus('failed')
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [taskId, indexAttempt])

  const handleSend = useCallback(
    async (value: string) => {
      const question = value.trim()
      if (!question || loading) return
      const controller = chatController.current
      if (!controller || controller.signal.aborted) return

      if (!selectedModel) {
        toast.error('请先选择可用的问答模型')
        return
      }

      addMessage(taskId, { role: 'user', content: question })
      setInput('')
      setLoading(true)

      try {
        const history = messages.map(m => ({ role: m.role, content: m.content }))
        await askQuestion({
          task_id: taskId,
          question,
          history,
          provider_id: selectedModel.provider_id,
          model_name: selectedModel.model_name,
        }, controller.signal, setProgress)
        if (!controller.signal.aborted) await loadHistory(taskId)
      } catch (error: any) {
        if (controller.signal.aborted || error?.name === 'AbortError') return
        toast.error(error?.msg || error?.message || '问答请求失败，请稍后重试')
        void loadHistory(taskId).catch(() => {})
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    },
    [loading, taskId, selectedModel, messages, addMessage, loadHistory],
  )

  // 转换为 Bubble.List 的数据格式
  const bubbleItems = useMemo(() => {
    const items = messages.map((msg, i) => ({
      key: `msg-${i}`,
      role: msg.role === 'user' ? ('user' as const) : ('ai' as const),
      content: msg.content,
      footer:
        msg.role === 'assistant' && msg.sources ? (
          <SourceBadges sources={msg.sources} />
        ) : undefined,
    }))

    if (loading) {
      items.push({
        key: 'loading',
        role: 'ai' as const,
        content: progress,
        loading: true,
      } as any)
    }

    return items
  }, [messages, loading, progress])

  // Bubble 角色配置
  const roles = useMemo(
    () => ({
      user: {
        placement: 'end' as const,
        avatar: (
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <UserRound className="h-4 w-4" />
          </div>
        ),
        variant: 'filled' as const,
        styles: { content: { background: '#6e7f64', color: '#fbf8f2' } },
      },
      ai: {
        placement: 'start' as const,
        avatar: (
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-neutral-500 text-white">
            <Bot className="h-4 w-4" />
          </div>
        ),
        variant: 'outlined' as const,
        contentRender: (content: any) => (
          <div className="markdown-body prose prose-sm max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:my-2">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {typeof content === 'string' ? content : String(content)}
            </ReactMarkdown>
          </div>
        ),
      },
    }),
    [],
  )

  if (indexStatus === null || indexStatus === 'indexing' || indexStatus === 'idle') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-neutral-400">
        <Loader2 className="h-6 w-6 animate-spin" />
        <div className="text-center">
          <p className="text-sm font-medium">正在索引笔记内容...</p>
          <p className="mt-1 text-xs">首次使用会建立搜索索引，完成后即可提问</p>
        </div>
      </div>
    )
  }

  if (indexStatus === 'failed') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-neutral-400">
        <span className="text-sm">索引失败，请重试</span>
        <Button
          size="sm"
          variant="outline"
          onClick={async () => {
            setIndexStatus('indexing')
            try {
              await indexTask(taskId)
              setIndexAttempt(attempt => attempt + 1)
            } catch {
              toast.error('索引请求失败')
              setIndexStatus('failed')
            }
          }}
        >
          重新索引
        </Button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col border-l">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-sm font-medium">AI 问答</span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-neutral-400 hover:text-neutral-600"
            onClick={() => onModeChange(mode === 'half' ? 'full' : 'half')}
            title={mode === 'half' ? '全屏' : '半屏'}
          >
            {mode === 'half' ? (
              <Maximize2 className="h-3.5 w-3.5" />
            ) : (
              <Minimize2 className="h-3.5 w-3.5" />
            )}
          </Button>
          {messages.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-neutral-400 hover:text-red-500"
              onClick={() => clearChat(taskId)}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 border-b px-3 py-2">
        <label htmlFor={modelSelectId} className="shrink-0 text-xs text-neutral-500">问答模型</label>
        <Select
          value={selectedModel ? String(selectedModel.id) : ''}
          onValueChange={value => selectModel(taskId, Number(value))}
          onOpenChange={open => {
            if (open) {
              void loadEnabledModels()
              void refreshQuotas()
            }
          }}
          disabled={loading || modelList.length === 0}
        >
          <SelectTrigger id={modelSelectId} size="sm" className="min-w-0 flex-1 truncate">
            <SelectValue placeholder={modelsLoading ? '正在加载模型…' : '暂无可用模型'}>{selectedModel?.model_name}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {modelList.map(model => (
              <SelectItem key={model.id} value={String(model.id)}>
                <span className="flex flex-col items-start gap-0.5">
                  <span>
                    {model.model_name}
                    {modelList.some(other => other.id !== model.id && other.model_name === model.model_name)
                      ? ` · ${model.provider_id}` : ''}
                  </span>
                  <ModelQuotaLabel providerId={model.provider_id} modelName={model.model_name} />
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* 消息列表 */}
      <div className="flex-1 overflow-hidden">
        {messages.length === 0 && !loading ? (
          <div className="flex h-full items-center justify-center text-center text-sm text-neutral-400">
            <div>
              <p>针对笔记内容提问</p>
              <p className="mt-1 text-xs">例如：这个视频的核心观点是什么？</p>
            </div>
          </div>
        ) : (
          <Bubble.List
            items={bubbleItems}
            role={roles}
            style={{ height: '100%' }}
          />
        )}
      </div>

      {/* 输入区域 */}
      <div className="border-t px-3 py-2">
        <Sender
          value={input}
          onChange={setInput}
          onSubmit={handleSend}
          loading={loading}
          disabled={!selectedModel}
          placeholder="输入你的问题..."
        />
      </div>
    </div>
  )
}
