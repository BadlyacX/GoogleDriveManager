import io
import os
import sys
import shutil
import time
import pickle
import uuid
from datetime import datetime, timezone
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_appdata_dir():
    if os.name == "nt":
        config_root = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        config_root = os.environ.get("XDG_CONFIG_HOME")
        if not config_root:
            config_root = os.path.join(os.path.expanduser("~"), ".config")

    base_dir = os.path.join(config_root, "GDM")
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

def get_token_path():
    return os.path.join(get_appdata_dir(), "token.pickle")

def get_credentials_path():
    return os.path.join(get_appdata_dir(), "credentials.json")

def ensure_credentials():
    target = get_credentials_path()

    if os.path.exists(target):
        return target

    if getattr(sys, 'frozen', False):
        source = os.path.join(sys._MEIPASS, "credentials.json")
    else:
        source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")

    if not os.path.exists(source):
        raise Exception(f"找不到內建 credentials.json: {source}")

    shutil.copy(source, target)
    return target

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
BASE_DIR = get_base_path()
cred_path = os.path.join(BASE_DIR, "credentials.json")

def parse_drive_time(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)

class DriveClient:
    def __init__(self):
        self.service = None

    def _auth(self):
        creds = None

        token_path = get_token_path()
        cred_path = ensure_credentials()

        if os.path.exists(token_path):
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(cred_path):
                    raise Exception(f"找不到 credentials.json（請放到 {get_appdata_dir()}）")

                flow = InstalledAppFlow.from_client_secrets_file(
                    cred_path,
                    SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(token_path, 'wb') as token:
                pickle.dump(creds, token)

        return build('drive', 'v3', credentials=creds)
    
    def has_token(self):
        return os.path.exists(get_token_path())
    
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
                fields="nextPageToken, files(id,name,mimeType,size,modifiedTime)",
                pageToken=page_token,
                orderBy="modifiedTime desc,name"
            ).execute()

            files.extend(res.get("files", []))
            page_token = res.get("nextPageToken")

            if not page_token:
                break

        files.sort(
            key=lambda file_data: (
                file_data.get("mimeType") != FOLDER_MIME,
                -parse_drive_time(file_data.get("modifiedTime")).timestamp(),
                file_data.get("name", "").lower(),
            )
        )

        return files

    def is_folder(self, file_data):
        return file_data.get("mimeType") == FOLDER_MIME

    def download_file(self, file_id, path, progress_callback=None, cancel_flag=None, retries=3):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

        for attempt in range(retries):
            temp_path = f"{path}.part-{uuid.uuid4().hex}"
            try:
                request = self.service.files().get_media(fileId=file_id)

                with io.FileIO(temp_path, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request, chunksize=1024*1024)

                    done = False
                    start_time = time.time()

                    while not done:
                        if cancel_flag and cancel_flag():
                            raise Exception("CANCELLED")

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

                os.replace(temp_path, path)
                if progress_callback:
                    progress_callback(100, os.path.getsize(path), os.path.getsize(path), 0, 0)
                return

            except Exception as e:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                if str(e) == "CANCELLED":
                    raise
                if attempt >= retries - 1:
                    raise
                time.sleep(2)

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
    
    def get_credentials_path(self):
        return get_credentials_path()
