import NoteHistory from '@/pages/HomePage/components/NoteHistory.tsx'
import { useTaskStore } from '@/store/taskStore'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
const History = () => {
  const currentTaskId = useTaskStore(state => state.currentTaskId)
  const setCurrentTask = useTaskStore(state => state.setCurrentTask)
  return (
    <>
      <div className={'flex h-full w-full flex-col gap-4 px-2.5 py-1.5'}>
        <ScrollArea className="w-full sm:h-[480px] md:h-[720px] lg:h-[92%]">
          <NoteHistory onSelect={setCurrentTask} selectedId={currentTaskId} />
        </ScrollArea>
      </div>
    </>
  )
}

export default History
