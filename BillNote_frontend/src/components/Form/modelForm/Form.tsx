import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useNavigate, useParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { ModelSelector } from '@/components/Form/modelForm/ModelSelector'
import { deleteModelById, importRecommendedModels, testConnection } from '@/services/model'
import { useModelStore } from '@/store/modelStore'
import { useProviderStore } from '@/store/providerStore'

const providerSchema = z.object({
  name: z.string().min(2, '名称不能少于 2 个字符'),
  apiKey: z.string().optional(),
  baseUrl: z.string().url('必须是合法 URL'),
  type: z.string(),
})
type ProviderFormValues = z.infer<typeof providerSchema>
type SavedModel = { id: number; model_name: string; supports_image_input: boolean | null }

const ProviderForm = ({ isCreate = false }: { isCreate?: boolean }) => {
  const { id } = useParams()
  const navigate = useNavigate()
  const loadProviderById = useProviderStore(state => state.loadProviderById)
  const updateProvider = useProviderStore(state => state.updateProvider)
  const addNewProvider = useProviderStore(state => state.addNewProvider)
  const loadModelsById = useModelStore(state => state.loadModelsById)
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [isBuiltIn, setIsBuiltIn] = useState(false)
  const [models, setModels] = useState<SavedModel[]>([])
  const providerForm = useForm<ProviderFormValues>({
    resolver: zodResolver(providerSchema),
    defaultValues: { name: '', apiKey: '', baseUrl: '', type: 'custom' },
  })

  useEffect(() => {
    let active = true
    const load = async () => {
      setLoading(true)
      try {
        if (!isCreate && id) {
          const provider = await loadProviderById(id)
          const saved = await loadModelsById(id)
          if (!active) return
          providerForm.reset({
            name: provider.name,
            apiKey: provider.apiKey,
            baseUrl: provider.baseUrl,
            type: provider.type,
          })
          setIsBuiltIn(provider.type === 'built-in')
          setModels(saved)
        } else {
          providerForm.reset({ name: '', apiKey: '', baseUrl: '', type: 'custom' })
          setIsBuiltIn(false)
          setModels([])
        }
      } catch {
        if (active) toast.error('加载模型供应商失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => { active = false }
  }, [id, isCreate])

  const refreshSavedModels = async () => {
    if (id) setModels(await loadModelsById(id))
  }

  const handleRecommendedImport = async () => {
    if (!id) return
    setImporting(true)
    try {
      await importRecommendedModels(id)
      await refreshSavedModels()
      await useModelStore.getState().loadEnabledModels()
      toast.success('已导入 5 个文本模型和 5 个可读图模型')
    } catch {
      toast.error('导入推荐模型失败')
    } finally {
      setImporting(false)
    }
  }

  const handleDelete = async (modelId: number) => {
    if (!window.confirm('确定要删除这个模型吗？')) return
    try {
      await deleteModelById(modelId)
      await refreshSavedModels()
      await useModelStore.getState().loadEnabledModels()
      toast.success('删除成功')
    } catch {
      toast.error('删除模型失败')
    }
  }

  const handleTest = async () => {
    if (!id) {
      toast.error('请先保存供应商信息')
      return
    }
    setTesting(true)
    try {
      await testConnection({ id })
      toast.success('测试连通性成功')
    } catch {
      toast.error('测试连通性失败，请检查 API Key、地址和已保存模型')
    } finally {
      setTesting(false)
    }
  }

  const onProviderSubmit = async (values: ProviderFormValues) => {
    try {
      if (!isCreate && id) {
        await updateProvider({ ...values, id })
        toast.success('更新供应商成功')
      } else {
        const newId = await addNewProvider(values)
        toast.success('新增供应商成功')
        navigate(`/settings/model/${newId}`)
      }
    } catch {
      toast.error('保存供应商失败')
    }
  }

  if (loading) return <div className="p-4">加载中...</div>

  return (
    <div className="flex flex-col gap-8 p-4">
      <Form {...providerForm}>
        <form onSubmit={providerForm.handleSubmit(onProviderSubmit)} className="flex max-w-xl flex-col gap-4">
          <div className="text-lg font-bold">{isCreate ? '新增模型供应商' : '编辑模型供应商'}</div>
          {!isBuiltIn && <p className="text-sm text-red-500 italic">自定义模型供应商需要兼容 OpenAI SDK</p>}
          <FormField control={providerForm.control} name="name" render={({ field }) => (
            <FormItem className="flex items-center gap-4">
              <FormLabel className="w-24 text-right">名称</FormLabel>
              <FormControl><Input {...field} disabled={isBuiltIn} className="flex-1" /></FormControl>
              <FormMessage />
            </FormItem>
          )} />
          <FormField control={providerForm.control} name="apiKey" render={({ field }) => (
            <FormItem className="flex items-center gap-4">
              <FormLabel className="w-24 text-right">API Key</FormLabel>
              <FormControl><Input {...field} className="flex-1" /></FormControl>
              <FormMessage />
            </FormItem>
          )} />
          <FormField control={providerForm.control} name="baseUrl" render={({ field }) => (
            <FormItem className="flex items-center gap-4">
              <FormLabel className="w-24 text-right">API地址</FormLabel>
              <FormControl><Input {...field} className="flex-1" /></FormControl>
              <Button type="button" onClick={handleTest} variant="ghost" disabled={testing || isCreate}>
                {testing ? '测试中...' : '测试连通性'}
              </Button>
              <FormMessage />
            </FormItem>
          )} />
          <FormField control={providerForm.control} name="type" render={({ field }) => (
            <FormItem className="flex items-center gap-4">
              <FormLabel className="w-24 text-right">类型</FormLabel>
              <FormControl><Input {...field} disabled className="flex-1" /></FormControl>
              <FormMessage />
            </FormItem>
          )} />
          <Button type="submit" disabled={!providerForm.formState.isDirty}>
            {isCreate ? '保存创建' : '保存修改'}
          </Button>
        </form>
      </Form>

      {!isCreate && id && (
        <div className="flex max-w-xl flex-col gap-5">
          <div className="flex flex-col gap-3">
            <h2 className="font-bold">模型列表</h2>
            {id === 'qwen' && (
              <Button type="button" onClick={handleRecommendedImport} disabled={importing}>
                {importing ? '导入中...' : '一键导入推荐的 10 个模型'}
              </Button>
            )}
            <ModelSelector providerId={id} onSaved={refreshSavedModels} />
          </div>
          <div className="flex flex-col gap-2">
            <h2 className="font-bold">已启用模型</h2>
            <div className="flex flex-wrap gap-2 rounded p-2.5">
              {models.map(model => (
                <span key={model.id} className="inline-flex items-center gap-1 rounded-md bg-sage px-2 py-0.5 text-sm text-primary">
                  {model.model_name}
                  <span className="text-xs">{model.supports_image_input === true ? '· 可读图' : model.supports_image_input === false ? '· 纯文本' : '· 能力待确认'}</span>
                  <button type="button" onClick={() => handleDelete(model.id)} aria-label={`删除 ${model.model_name}`} className="hover:text-foreground">
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ProviderForm
