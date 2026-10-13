import { create } from 'zustand'
import request from '@/utils/request'
import { getAuthToken } from '@/utils/authSession'
import type { ChatSource } from '@/services/chat'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: ChatSource[]
}

interface ChatState {
  chatHistory: Record<string, ChatMessage[]>
  addMessage: (taskId: string, message: ChatMessage) => void
  clearChat: (taskId: string) => Promise<void>
  loadHistory: (taskId: string) => Promise<void>
  getMessages: (taskId: string) => ChatMessage[]
}

// The server owns chat history. Private content is never hydrated from shared storage.
export const useChatStore = create<ChatState>((set, get) => ({
  chatHistory: {},
  addMessage: (taskId, message) => set(state => ({ chatHistory: {
    ...state.chatHistory, [taskId]: [...(state.chatHistory[taskId] || []), message],
  } })),
  loadHistory: async taskId => {
    const token = getAuthToken()
    const messages = await request.get<unknown, ChatMessage[]>('/chat/history', { params: { task_id: taskId } })
    if (token !== getAuthToken()) return
    set(state => ({ chatHistory: { ...state.chatHistory, [taskId]: messages } }))
  },
  clearChat: async taskId => {
    const token = getAuthToken()
    try {
      await request.delete('/chat/history', { params: { task_id: taskId } })
      if (token !== getAuthToken()) return
      set(state => {
        const history = { ...state.chatHistory }
        delete history[taskId]
        return { chatHistory: history }
      })
    } catch { /* Keep the history when the server rejects the request. */ }
  },
  getMessages: taskId => get().chatHistory[taskId] || [],
}))
