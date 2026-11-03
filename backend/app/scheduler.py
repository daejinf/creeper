from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler


scheduler = BackgroundScheduler(timezone="Asia/Seoul")


def heartbeat() -> None:
    # M0 keeps a scheduler lifecycle in place. User-facing reminders are added in M6.
    _ = datetime.now(timezone.utc)


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(heartbeat, "interval", minutes=30, id="system-heartbeat", replace_existing=True)
        scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)

