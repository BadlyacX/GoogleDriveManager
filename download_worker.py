import os
import time
import queue
from PySide6.QtCore import QThread, Signal

FOLDER_MIME = "application/vnd.google-apps.folder"


class DownloadWorker(QThread):
    task_started = Signal(str)
    task_progress = Signal(str, int, str, str)
    task_finished = Signal(str)
    task_cancelled = Signal(str)
    task_error = Signal(str, str)

    def __init__(self, client, task_queue):
        super().__init__()
        self.client = client
        self.task_queue = task_queue
        self.running = True
        self.cancelled_tasks = set()

    def run(self):
        while self.running:
            try:
                task = self.task_queue.get_nowait()
            except queue.Empty:
                break

            task_id = task["task_id"]
            file_data = task["file"]
            target_path = task["target_path"]

            try:
                if self.is_cancelled(task_id):
                    self.task_cancelled.emit(task_id)
                    continue

                self.task_started.emit(task_id)

                if file_data.get("mimeType") == FOLDER_MIME:
                    self.download_folder(file_data, target_path, task_id)
                else:
                    self.download_single_file(file_data, target_path, task_id)

                if self.is_cancelled(task_id):
                    self.cleanup_partial_file(target_path)
                    self.task_cancelled.emit(task_id)
                else:
                    self.task_finished.emit(task_id)

            except Exception as e:
                if str(e) == "CANCELLED":
                    self.cleanup_partial_file(target_path)
                    self.task_cancelled.emit(task_id)
                else:
                    self.task_error.emit(task_id, str(e))

            finally:
                self.task_queue.task_done()

    def download_single_file(self, file_data, target_path, task_id):
        retries = 3

        for attempt in range(retries):
            if self.is_cancelled(task_id):
                raise Exception("CANCELLED")

            try:
                def on_progress(percent, downloaded, total, speed, eta):
                    if self.is_cancelled(task_id):
                        raise Exception("CANCELLED")

                    self.task_progress.emit(
                        task_id,
                        percent,
                        self.format_speed(speed),
                        self.format_eta(eta)
                    )

                self.client.download_file(
                    file_data["id"],
                    target_path,
                    progress_callback=on_progress,
                    cancel_flag=lambda: self.is_cancelled(task_id)
                )

                return

            except Exception as e:
                if str(e) == "CANCELLED":
                    raise

                if attempt >= retries - 1:
                    raise

                time.sleep(1)

    def download_folder(self, folder_data, target_path, task_id):
        if self.is_cancelled(task_id):
            raise Exception("CANCELLED")

        os.makedirs(target_path, exist_ok=True)
        children = self.client.list_files(folder_data["id"])

        total = len(children)
        if total == 0:
            self.task_progress.emit(task_id, 100, "-", "0s")
            return

        for index, child in enumerate(children, start=1):
            if self.is_cancelled(task_id):
                raise Exception("CANCELLED")

            child_path = os.path.join(target_path, child["name"])

            if child.get("mimeType") == FOLDER_MIME:
                self.download_folder(child, child_path, task_id)
            else:
                self.client.download_file(
                    child["id"],
                    child_path,
                    cancel_flag=lambda: self.is_cancelled(task_id)
                )

            percent = int(index / total * 100)
            self.task_progress.emit(task_id, percent, "-", "-")

    def cancel_task(self, task_id):
        self.cancelled_tasks.add(task_id)

    def is_cancelled(self, task_id):
        return (not self.running) or (task_id in self.cancelled_tasks)

    @staticmethod
    def cleanup_partial_file(path):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass

    @staticmethod
    def format_speed(bytes_per_second):
        if bytes_per_second >= 1024 * 1024:
            return f"{bytes_per_second / 1024 / 1024:.2f} MB/s"
        if bytes_per_second >= 1024:
            return f"{bytes_per_second / 1024:.2f} KB/s"
        return f"{bytes_per_second:.0f} B/s"

    @staticmethod
    def format_eta(seconds):
        if seconds <= 0:
            return "0s"

        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {sec}s"
        return f"{sec}s"

    def stop(self):
        self.running = False