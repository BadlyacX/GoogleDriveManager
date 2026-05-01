import os
import sys
import uuid
import queue

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QLabel,
    QLineEdit
)

from drive_client import DriveClient, FOLDER_MIME
from download_worker import DownloadWorker
from drive_client import get_token_path

class DriveTree(QTreeWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent_window = parent

        self.setAcceptDrops(True)

        self.itemExpanded.connect(self.on_item_expanded)

        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def on_item_expanded(self, item):
        if item.data(0, Qt.UserRole + 1) == "loaded":
            return

        file_data = item.data(0, Qt.UserRole)
        if not file_data:
            return

        folder_id = file_data.get("id")

        item.takeChildren()

        self.parent_window.load_children(item, folder_id)

        item.setData(0, Qt.UserRole + 1, "loaded")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()

    def dropEvent(self, event):
        item = self.currentItem()
        if not item:
            return

        data = item.data(0, Qt.UserRole)
        if data.get("mimeType") != FOLDER_MIME:
            return

        folder_id = data["id"]

        for url in event.mimeData().urls():
            path = url.toLocalFile()
            self.parent_window.upload_to_folder(path, folder_id)

class TaskTable(QTableWidget):
    COL_NAME = 0
    COL_PROGRESS = 1
    COL_SPEED = 2
    COL_ETA = 3
    COL_STATUS = 4

    def __init__(self):
        super().__init__(0, 7)
        self.setHorizontalHeaderLabels([
            "檔案", "進度", "速度", "ETA", 
            "狀態", "取消", "重試"
            ])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.task_rows = {}

    def add_task(self, task_id, name, cancel_cb, retry_cb):
        row = self.rowCount()
        self.insertRow(row)

        self.setItem(row, 0, QTableWidgetItem(name))

        bar = QProgressBar()
        self.setCellWidget(row, 1, bar)

        self.setItem(row, 2, QTableWidgetItem("-"))
        self.setItem(row, 3, QTableWidgetItem("-"))
        self.setItem(row, 4, QTableWidgetItem("等待中"))

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(lambda: cancel_cb(task_id))
        self.setCellWidget(row, 5, btn_cancel)

        btn_retry = QPushButton("重試")
        btn_retry.clicked.connect(lambda: retry_cb(task_id))
        self.setCellWidget(row, 6, btn_retry)

        self.task_rows[task_id] = row

    def set_status(self, task_id, status):
        row = self.task_rows.get(task_id)
        if row is None:
            return

        self.item(row, self.COL_STATUS).setText(status)

    def set_progress(self, task_id, percent, speed, eta):
        row = self.task_rows.get(task_id)
        if row is None:
            return

        bar = self.cellWidget(row, self.COL_PROGRESS)
        bar.setValue(percent)
        self.item(row, self.COL_SPEED).setText(speed)
        self.item(row, self.COL_ETA).setText(eta)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Drive Manager Pro")
        self.resize(1100, 700)

        self.client = DriveClient()
        
        logged_in = self.client.try_auto_login()

        self.task_queue = queue.Queue()
        self.workers = []
        self.max_workers = 3

        self.tree = DriveTree(self)
        self.tasks = TaskTable()

        self.search_box = QLineEdit()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_download = QPushButton("Download Selected")
        self.btn_delete = QPushButton("Delete Selected")
        self.btn_upload_file = QPushButton("Upload File")
        self.btn_upload_folder = QPushButton("Upload Folder")

        self.search_box.setPlaceholderText("搜尋檔案...")

        self.tree.setHeaderLabel("按住Ctrl可多選項目")

        self.btn_refresh.clicked.connect(self.load_root)
        self.btn_download.clicked.connect(self.download_selected)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_upload_file.clicked.connect(self.upload_file)
        self.btn_upload_folder.clicked.connect(self.upload_folder)
        self.search_box.returnPressed.connect(self.search)

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_download)
        buttons.addWidget(self.btn_delete)
        buttons.addWidget(self.btn_upload_file)
        buttons.addWidget(self.btn_upload_folder)
        buttons.addWidget(self.search_box)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.tasks)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout()
        layout.addLayout(buttons)
        layout.addWidget(splitter)

        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        self.client = DriveClient()
        self.client.service = None
        
        self.btn_login = QPushButton("登入 google")
        self.btn_login.clicked.connect(self.login)

        self.label_user = QLabel("未登入")

        if logged_in:
            email = self.client.get_user_email()
            self.label_user.setText(f"已登入：{email}")
            self.btn_login.hide()
        else:
            self.label_user.setText("未登入")

        buttons.addWidget(self.btn_login)
        buttons.addWidget(self.label_user)

        self.load_root()
        QTimer.singleShot(0, self.initialize_app)
        QTimer.singleShot(0, self.auto_login)

    def initialize_app(self):
        if os.path.exists("token.pickle"):
            try:
                self.client.service = self.client._auth()

                email = self.client.get_user_email()
                self.label_user.setText(f"已登入：{email}")
                self.btn_login.hide()

                self.load_root()

                return

            except Exception as e:
                print("Auto login failed:", e)

        self.label_user.setText("未登入")
   
    def load_root(self):
        self.tree.clear()

        root_data = {
            "id": "root",
            "name": "My Drive",
            "mimeType": FOLDER_MIME
        }

        root = QTreeWidgetItem(["My Drive"])
        root.setData(0, Qt.UserRole, root_data)
        root.setData(0, Qt.UserRole + 1, "not_loaded")
        root.addChild(QTreeWidgetItem(["Loading..."]))
        self.tree.addTopLevelItem(root)
        root.setExpanded(True)

    def load_children(self, parent_item, folder_id):
        if not self.client.service:
            return
        try:
            files = self.client.list_files(folder_id)

            for file_data in files:
                item = QTreeWidgetItem([file_data["name"]])
                item.setData(0, Qt.UserRole, file_data)

                if file_data.get("mimeType") == FOLDER_MIME:
                    item.setData(0, Qt.UserRole + 1, "not_loaded")
                    item.addChild(QTreeWidgetItem(["Loading..."]))
                else:
                    item.setData(0, Qt.UserRole + 1, "file")

                parent_item.addChild(item)

        except Exception as e:
            QMessageBox.warning(self, "載入失敗", str(e))

    def selected_files(self):
        result = []

        for item in self.tree.selectedItems():
            file_data = item.data(0, Qt.UserRole)
            if file_data:
                result.append(file_data)

        return result

    def download_selected(self):
        if not self.client.service:
            QMessageBox.warning(self, "未登入", "請先登入")
            return

        files = self.selected_files()
        if not files:
            return

        target_dir = QFileDialog.getExistingDirectory(self, "選擇下載資料夾")
        if not target_dir:
            return

        for file_data in files:
            task_id = str(uuid.uuid4())
            path = os.path.join(target_dir, file_data["name"])

            task = {
                "task_id": task_id,
                "file": file_data,
                "target_path": path
            }

            self.task_queue.put(task)

            self.tasks.add_task(
                task_id,
                file_data["name"],
                self.cancel_task,
                self.retry_task
            )

        self.start_workers()

    def start_workers(self):
        active = [w for w in self.workers if w.isRunning()]
        available_slots = self.max_workers - len(active)

        for _ in range(max(0, available_slots)):
            if self.task_queue.empty():
                break

            worker = DownloadWorker(self.client, self.task_queue)
            worker.task_started.connect(
                lambda task_id: self.tasks.set_status(task_id, "下載中")
                )
            worker.task_progress.connect(self.tasks.set_progress)
            worker.task_finished.connect(
                lambda task_id: self.tasks.set_status(task_id, "完成")
                )
            worker.task_error.connect(self.on_task_error)
            worker.finished.connect(self.on_worker_finished)

            self.workers.append(worker)
            worker.start()

    def on_worker_finished(self):
        self.workers = [w for w in self.workers if w.isRunning()]

        if not self.task_queue.empty():
            self.start_workers()

    def on_task_error(self, task_id, message):
        self.tasks.set_status(task_id, "錯誤")
        QMessageBox.warning(self, "下載錯誤", message)

    def delete_selected(self):
        files = self.selected_files()
        if not files:
            return

        reply = QMessageBox.question(
            self,
            "確認刪除",
            f"確定要刪除 {len(files)} 個項目？",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        for file_data in files:
            try:
                self.client.delete_file(file_data["id"])
            except Exception as e:
                QMessageBox.warning(self, "刪除失敗", f"{file_data['name']}\n{e}")

        self.load_root()

    def current_parent_id(self):
        items = self.tree.selectedItems()

        if not items:
            return "root"

        file_data = items[0].data(0, Qt.UserRole)

        if file_data and file_data.get("mimeType") == FOLDER_MIME:
            return file_data["id"]

        parent = items[0].parent()
        if parent:
            parent_data = parent.data(0, Qt.UserRole)
            return parent_data["id"]

        return "root"

    def upload_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "選擇要上傳的檔案")
        if not paths:
            return

        parent_id = self.current_parent_id()

        errors = []
        for path in paths:
            try:
                self.client.upload_file(path, parent_id)
            except Exception as e:
                errors.append(f"{path}: {e}")

        if errors:
            QMessageBox.warning(self, "上傳錯誤", "\n".join(errors))

        self.load_root()

    def upload_folder(self):
        path = QFileDialog.getExistingDirectory(self, "選擇要上傳的資料夾")
        if not path:
            return

        try:
            self.client.upload_folder(path, self.current_parent_id())
        except Exception as e:
            QMessageBox.warning(self, "上傳錯誤", str(e))

        self.load_root()

    def upload_to_folder(self, path, folder_id):
        try:
            if os.path.isdir(path):
                self.client.upload_folder(path, folder_id)
            else:
                self.client.upload_file(path, folder_id)
        except Exception as e:
            QMessageBox.warning(self, "上傳錯誤", str(e))

        self.load_root()

    def login(self):
        try:
            self.client.ensure_auth()
            email = self.client.get_user_email()

            self.label_user.setText(f"已登入：{email}")
            self.btn_login.hide()

            self.load_root()
        except Exception as e:
            QMessageBox.warning(self, "登入失敗", str(e))

    def cancel_task(self, task_id):
        for w in self.workers:
            w.stop()
        self.tasks.set_status(task_id, "已取消")

    def retry_task(self, task_id):
        row = self.tasks.task_rows.get(task_id)
        if row is None:
            return

        name = self.tasks.item(row, 0).text()

        for file_data in self.selected_files():
            if file_data["name"] == name:
                new_id = str(uuid.uuid4())

                self.task_queue.put({
                    "task_id": new_id,
                    "file": file_data,
                    "target_path": file_data["name"]
                })

                self.tasks.add_task(
                    new_id,
                    name,
                    self.cancel_task,
                    self.retry_task
                )

                self.start_workers()

    def search(self):
        text = self.search_box.text().strip()
        if not text:
            return

        self.tree.clear()

        try:
            results = self.client.search_files(text)

            for f in results:
                item = QTreeWidgetItem([f["name"]])
                item.setData(0, Qt.UserRole, f)

                if f.get("mimeType") == FOLDER_MIME:
                    item.setData(0, Qt.UserRole + 1, "not_loaded")
                    item.addChild(QTreeWidgetItem(["Loading..."]))
                else:
                    item.setData(0, Qt.UserRole + 1, "file")

                self.tree.addTopLevelItem(item)

        except Exception as e:
            QMessageBox.warning(self, "搜尋錯誤", str(e))

    def auto_login(self):
        try:
            self.client.service = self.client._auth()
            email = self.client.get_user_email()

            self.label_user.setText(f"已登入: {email}")
            self.btn_login.hide()

            self.load_root()

        except Exception as e:
            print("Auto login failed:", e)

            token_path = get_token_path()
            if os.path.exists(token_path):
                os.remove(token_path)

            self.label_user.setText("未登入")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

# pyinstaller --onefile --windowed --collect-all google --hidden-import googleapiclient.discovery --hidden-import google_auth_oauthlib.flow --hidden-import google.auth.transport.requests main.py