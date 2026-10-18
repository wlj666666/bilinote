import enum


class TaskStatus(str, enum.Enum):
    WAITING_RESOURCE = "WAITING_RESOURCE"
    WAITING_RETRY = "WAITING_RETRY"
    CANCELLED = "CANCELLED"
    PENDING = "PENDING"
    PARSING = "PARSING"
    DOWNLOADING = "DOWNLOADING"
    DETECTING_SUBTITLES = "DETECTING_SUBTITLES"
    EXTRACTING_SUBTITLES = "EXTRACTING_SUBTITLES"
    TRANSCRIBING = "TRANSCRIBING"
    SUMMARIZING = "SUMMARIZING"
    FORMATTING = "FORMATTING"
    SAVING = "SAVING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

    @classmethod
    def description(cls, status):
        desc_map = {
            cls.WAITING_RESOURCE: "等待资源",
            cls.WAITING_RETRY: "等待重试",
            cls.CANCELLED: "已取消",
            cls.PENDING: "排队中",
            cls.PARSING: "解析链接",
            cls.DOWNLOADING: "下载中",
            cls.DETECTING_SUBTITLES: "定位字幕",
            cls.EXTRACTING_SUBTITLES: "提取字幕",
            cls.TRANSCRIBING: "转录中",
            cls.SUMMARIZING: "总结中",
            cls.FORMATTING: "格式化中",
            cls.SAVING: "保存中",
            cls.SUCCESS: "完成",
            cls.FAILED: "失败",
        }
        return desc_map.get(status, "未知状态")
