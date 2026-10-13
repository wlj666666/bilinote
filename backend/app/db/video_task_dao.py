from app.db.models.video_tasks import VideoTask
from app.db.models.user_data import DeletedTask
from app.db.engine import get_db
from app.utils.logger import get_logger
from app.services.storage_scope import current_scope

logger = get_logger(__name__)


# 插入任务
def insert_video_task(video_id: str, platform: str, task_id: str):
    db = next(get_db())
    try:
        if db.get(DeletedTask, task_id) is not None:
            return
        task = db.query(VideoTask).filter_by(task_id=task_id).first()
        if task:
            task.video_id = video_id
            task.platform = platform
        else:
            scope = current_scope.get()
            task = VideoTask(video_id=video_id, platform=platform, task_id=task_id,
                             user_id=scope.user_id if scope else None,
                             storage_relpath=f'users/{scope.user_id}' if scope else '')
            db.add(task)
        db.commit()
        db.refresh(task)
        logger.info(f"Video task inserted successfully. video_id: {video_id}, platform: {platform}, task_id: {task_id}")
    except Exception as e:
        logger.error(f"Failed to insert video task: {e}")
    finally:
        db.close()


# 查询任务（最新一条）
def get_task_by_video(video_id: str, platform: str):
    db = next(get_db())
    try:
        task = (
            db.query(VideoTask)
            .filter_by(video_id=video_id, platform=platform)
            .order_by(VideoTask.created_at.desc())
            .first()
        )
        if task:
            logger.info(f"Task found for video_id: {video_id} and platform: {platform}")
            return task.task_id
        else:
            logger.info(f"No task found for video_id: {video_id} and platform: {platform}")
            return None
    except Exception as e:
        logger.error(f"Failed to get task by video: {e}")
    finally:
        db.close()


# 删除任务
def delete_task_by_video(video_id: str, platform: str):
    db = next(get_db())
    try:
        tasks = (
            db.query(VideoTask)
            .filter_by(video_id=video_id, platform=platform)
            .all()
        )
        for task in tasks:
            db.delete(task)
        db.commit()
        logger.info(f"Task(s) deleted for video_id: {video_id} and platform: {platform}")
    except Exception as e:
        logger.error(f"Failed to delete task by video: {e}")
    finally:
        db.close()
