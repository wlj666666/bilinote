import { create } from 'zustand'
import type { IProvider } from '@/types'
import {
  addProvider,
  getProviderById,
  getProviderList,
  updateProviderById,
} from '@/services/model.ts'

type ProviderInput = Partial<IProvider> & Pick<IProvider, 'name' | 'type' | 'baseUrl'>
type ProviderUpdate = Partial<IProvider> & Pick<IProvider, 'id'>

interface ProviderStore {
  provider: IProvider[]
  setProvider: (provider: IProvider) => void
  setAllProviders: (providers: IProvider[]) => void
  getProviderById: (id: string) => IProvider | undefined
  getProviderList: () => IProvider[]
  fetchProviderList: () => Promise<void>
  loadProviderById: (id: string) => Promise<IProvider>
  addNewProvider: (provider: ProviderInput) => Promise<string>
  updateProvider: (provider: ProviderUpdate) => Promise<void>
}

const fromApi = (item: any): IProvider => ({
  id: item.id,
  name: item.name,
  logo: item.logo,
  apiKey: item.api_key,
  baseUrl: item.base_url,
  type: item.type,
  enabled: item.enabled,
})

export const useProviderStore = create<ProviderStore>((set, get) => ({
  provider: [],
  setProvider: newProvider => set(state => ({
    provider: state.provider.some(p => p.id === newProvider.id)
      ? state.provider.map(p => p.id === newProvider.id ? newProvider : p)
      : [...state.provider, newProvider],
  })),
  setAllProviders: providers => set({ provider: providers }),
  getProviderById: id => get().provider.find(p => p.id === id),
  getProviderList: () => get().provider,
  loadProviderById: async id => fromApi(await getProviderById(id)),
  addNewProvider: async provider => {
    const id = await addProvider({
      name: provider.name,
      type: provider.type,
      logo: provider.logo ?? 'custom',
      api_key: provider.apiKey ?? '',
      base_url: provider.baseUrl,
    }) as unknown as string
    await get().fetchProviderList()
    return id
  },
  updateProvider: async provider => {
    await updateProviderById({
      id: provider.id,
      name: provider.name,
      type: provider.type,
      logo: provider.logo,
      enabled: provider.enabled,
      api_key: provider.apiKey,
      base_url: provider.baseUrl,
    })
    await get().fetchProviderList()
  },
  fetchProviderList: async () => {
    const items = await getProviderList() as unknown as any[]
    set({ provider: items.map(fromApi) })
  },
}))
