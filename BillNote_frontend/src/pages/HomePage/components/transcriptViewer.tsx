"use client"

import { useTaskStore } from "@/store/taskStore"
import { useState } from "react"
import { cn } from "@/lib/utils"
import {ScrollArea} from "@/components/ui/scroll-area.tsx";

const TranscriptViewer = () => {
  const task = useTaskStore(state => state.tasks.find(item => item.id === state.currentTaskId))
  const [activeSegment, setActiveSegment] = useState<number | null>(null)
  const segments = task?.transcript?.segments || []

  const formatTime = (seconds: number): string => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, "0")}`
  }

  const handleSegmentClick = (index: number) => {
    setActiveSegment(index)
    // Here you could add functionality to play the audio from this segment
  }

  return (
      <div className="transcript-viewer flex h-full w-full flex-col  rounded-md border border-border bg-paper p-4">
        <h2 className="mb-2 text-lg font-medium">提取原文</h2>
        <p className="mb-4 text-xs text-muted-foreground">文本来源：{({ hard_subtitle_ocr: '画面字幕 OCR', asr: '语音转写', platform_subtitle: '平台字幕', client_prefetched: '平台字幕（浏览器预取）' } as Record<string, string>)[task?.transcript?.raw?.source || ''] || '历史记录（未标记来源）'}</p>
        {task?.transcript?.raw?.source === 'hard_subtitle_ocr' && task.transcript.raw.text_gap_seconds > 0 && (
          <p className="mb-3 text-xs text-amber-700">有 {Math.round(task.transcript.raw.text_gap_seconds)} 秒的较长字幕空白，可能是停顿或转场，请结合原视频核对。</p>
        )}
        {!segments.length ? (
            <div className="flex h-full items-center justify-center text-muted-foreground">暂无转写内容</div>
        ) : (
            <>


            <div className="mb-3 grid grid-cols-[80px_1fr] gap-2 border-b pb-2 text-xs font-medium text-muted-foreground">
                <div>时间</div>
                <div>内容</div>
              </div>
            <ScrollArea className="w-full overflow-y-auto">

              <div className="space-y-1">
                {segments.map((segment, index) => (
                    <div
                        key={index}
                        className={cn(
                            "group grid grid-cols-[80px_1fr] gap-2 rounded-md p-2 transition-colors hover:bg-slate-50",
                            activeSegment === index && "bg-slate-100",
                        )}
                        onClick={() => handleSegmentClick(index)}
                    >
                      <div className="flex items-center gap-1 text-xs text-slate-500">
                        <button
                            className="invisible rounded-full p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700 group-hover:visible"
                            onClick={(e) => {
                              e.stopPropagation()
                              // Add play functionality here
                            }}
                        >
                          {/*<Play className="h-3 w-3" />*/}
                        </button>
                        <span>{formatTime(segment.start)}</span>
                      </div>

                      <div className="text-sm leading-relaxed text-slate-700">
                        {segment.speaker && (
                            <span className="mr-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs font-medium text-slate-700">
                      {segment.speaker}
                    </span>
                        )}
                        {segment.text}
                      </div>
                    </div>
                ))}
              </div>
            </ScrollArea>

            </>
        )}


        {segments.length > 0 && (
            <div className="mt-4 flex justify-between border-t pt-3 text-xs text-slate-500">
              <span>共 {segments.length} 条片段</span>
              <span>总时长: {formatTime(segments[segments.length - 1]?.end || 0)}</span>
            </div>
        )}
      </div>
  )
}

export default TranscriptViewer
