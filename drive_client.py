import io
import os
import time
import pickle
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
cred_path = os.path.join(BASE_DIR, "credentials.json")

class DriveClient:
    def __init__(self):
        self.service = None

    def _auth(self):
        creds = None

        if os.path.exists("token.pickle"):
            with open("token.pickle", "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    cred_path,
                    SCOPES
                )
                creds = flow.run_local_server(port=0, open_browser=True)

            with open("token.pickle", "wb") as f:
                pickle.dump(creds, f)

        return build("drive", "v3", credentials=creds)
    
    def has_token(self):
        return os.path.exists("token.pickle")
    
    def try_auto_login(self):
        if not self.has_token():
            return False
        try:
            self.service = self._auth()
            return True
        
        except:
            return False

    def list_files(self, folder_id="root"):
        if self.service is None:
            raise Exception("尚未登入")
        
        files = []
        page_token = None

        while True:
            res = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,size)",
                pageToken=page_token,
                orderBy="folder,name"
            ).execute()

            files.extend(res.get("files", []))
            page_token = res.get("nextPageToken")

            if not page_token:
                break

        return files

    def is_folder(self, file_data):
        return file_data.get("mimeType") == FOLDER_MIME

    def download_file(self, file_id, path, progress_callback=None, retries=3):
        os.makedirs(os.path.dirname(path), exist_ok=True)

        for attempt in range(retries):
            try:
                request = self.service.files().get_media(fileId=file_id)

                with io.FileIO(path, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024)

                    done = False
                    start_time = time.time()
                    last_bytes = 0

                    while not done:
                        status, done = downloader.next_chunk()

                        if status and progress_callback:
                            progress = int(status.progress() * 100)
                            downloaded = status.resumable_progress
                            total = status.total_size or 0

                            elapsed = max(time.time() - start_time, 0.001)
                            speed = downloaded / elapsed

                            remaining = max(total - downloaded, 0)
                            eta = int(remaining / speed) if speed > 0 else 0

                            progress_callback(progress, downloaded, total, speed, eta)

                return

            except Exception:
                if attempt >= retries - 1:
                    raise
                time.sleep(2 * (attempt + 1))

    def create_folder(self, name, parent="root"):
        meta = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent]
        }

        return self.service.files().create(
            body=meta,
            fields="id,name,mimeType"
        ).execute()

    def upload_file(self, path, parent="root"):
        meta = {
            "name": os.path.basename(path),
            "parents": [parent]
        }

        media = MediaFileUpload(path, resumable=True)

        return self.service.files().create(
            body=meta,
            media_body=media,
            fields="id,name,mimeType,size"
        ).execute()

    def upload_folder(self, folder_path, parent="root"):
        folder = self.create_folder(os.path.basename(folder_path), parent)

        for name in os.listdir(folder_path):
            full_path = os.path.join(folder_path, name)

            if os.path.isdir(full_path):
                self.upload_folder(full_path, folder["id"])
            else:
                self.upload_file(full_path, folder["id"])

        return folder

    def delete_file(self, file_id):
        self.service.files().delete(fileId=file_id).execute()

    # 新增
    def ensure_auth(self):
        if not hasattr(self, "service") or self.service is None:
         self.service = self._auth()

    def get_user_email(self):
        try:
            info = self.service.about().get(fields="user").execute()
            return info.get("user", {}).get("emailAddress", "")
        except:
            return ""

    def search_files(self, query, parent="root"):
        q = f"name contains '{query}' and '{parent}' in parents and trashed=false"
        res = self.service.files().list(
            q=q,
            fields="files(id,name,mimeType,size)"
        ).execute()
        return res.get("files", [])