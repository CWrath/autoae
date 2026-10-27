"""Browser sign-in (OAuth + PKCE) and file upload for Dropbox and Google Drive. Standard library only."""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REDIRECT_PORT = 53682
REDIRECT_URI = "http://127.0.0.1:{}/".format(REDIRECT_PORT)
SMALL_UPLOAD = 32 * 1024 * 1024
CHUNK = 16 * 1024 * 1024
DRIVE_FOLDER = "application/vnd.google-apps.folder"

PROVIDERS = {
    "dropbox": {
        "label": "Dropbox",
        "auth_url": "https://www.dropbox.com/oauth2/authorize",
        "token_url": "https://api.dropbox.com/oauth2/token",
        "extra_auth": {"token_access_type": "offline"},
        "needs_secret": False,
    },
    "google_drive": {
        "label": "Google Drive",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "extra_auth": {"scope": "https://www.googleapis.com/auth/drive.file",
                       "access_type": "offline", "prompt": "consent"},
        "needs_secret": True,
    },
}


# App credentials shipped with Auto AE so users only click "Sign in". Desktop app IDs are not secret.
# Fill these in after registering the apps (see the Setup help in the Project Backup tool).
DEFAULT_CLIENTS = {
    "dropbox": {"client_id": "", "client_secret": ""},
    "google_drive": {"client_id": "", "client_secret": ""},
}


class CloudError(Exception):
    pass


class AuthError(CloudError):
    """The sign-in is missing, expired or revoked: the user has to connect again."""


def default_token_file():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "AUTO_AE", "cloud_tokens.json")


def split_remote_path(text):
    """'/Backups\\Projects/' -> ['Backups', 'Projects']"""
    return [p for p in (text or "").replace("\\", "/").split("/") if p.strip()]


def make_pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _message(body):
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return body.decode("utf-8", "replace")[:200]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return error.get("message") or json.dumps(error)[:200]
        return data.get("error_summary") or data.get("error_description") or str(error or data)[:200]
    return str(data)[:200]


def _http(method, url, headers=None, data=None, timeout=60, retries=4):
    """Returns (status, headers, body). Retries throttling and network hiccups; 401 raises AuthError."""
    for attempt in range(retries):
        request = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            if error.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(float(error.headers.get("Retry-After") or 2 ** attempt), 30))
            elif error.code == 401:
                raise AuthError("Sign-in expired. Connect again.") from None
            else:
                raise CloudError("{}: {}".format(error.code, _message(body))) from None
        except urllib.error.URLError as error:
            if attempt == retries - 1:
                raise CloudError("Network error: {}".format(error.reason)) from None
            time.sleep(2 ** attempt)
        if hasattr(data, "seek"):
            data.seek(0)


def _post_form(url, fields):
    body = urllib.parse.urlencode(fields).encode("ascii")
    try:
        _, _, raw = _http("POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    except AuthError:
        raise
    except CloudError as error:
        if str(error).startswith("400"):
            raise AuthError("Sign-in was rejected ({}). Connect again.".format(error)) from None
        raise
    return json.loads(raw.decode("utf-8"))


class TokenStore:
    def __init__(self, path=None):
        self.path = path or default_token_file()
        self._lock = threading.Lock()

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def get(self, provider):
        with self._lock:
            return self._read().get(provider) or {}

    def put(self, provider, record):
        with self._lock:
            data = self._read()
            data[provider] = record
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)

    def remove(self, provider):
        with self._lock:
            data = self._read()
            if data.pop(provider, None) is not None:
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2)


class _CallbackServer(http.server.HTTPServer):
    result = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        ok = "code" in query or "error" in query
        if ok:
            self.server.result = query
        text = ("<h2>Connected to Auto AE</h2><p>You can close this tab and return to the app.</p>"
                if "code" in query else
                "<h2>Sign-in didn't complete</h2><p>Return to Auto AE and try again.</p>")
        body = "<html><body style='font-family:sans-serif;padding:40px'>{}</body></html>".format(text).encode()
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Account:
    """One provider sign-in. Tokens live in the TokenStore; the app credentials come from the user's own API app."""

    def __init__(self, provider, client_id, client_secret="", store=None):
        if provider not in PROVIDERS:
            raise CloudError("Unknown provider: {}".format(provider))
        self.provider = provider
        self.spec = PROVIDERS[provider]
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.builtin = not self.client_id and bool(DEFAULT_CLIENTS[provider]["client_id"])
        if self.builtin:
            self.client_id = DEFAULT_CLIENTS[provider]["client_id"]
            self.client_secret = DEFAULT_CLIENTS[provider]["client_secret"]
        self.store = store or TokenStore()

    def credentials_missing(self):
        if not self.client_id:
            return "Enter the {} first.".format("app key" if self.provider == "dropbox" else "client ID")
        if self.spec["needs_secret"] and not self.client_secret:
            return "Enter the client secret first."
        return ""

    def is_connected(self):
        return bool(self.store.get(self.provider).get("refresh_token"))

    def connected_as(self):
        return self.store.get(self.provider).get("account", "")

    def disconnect(self):
        self.store.remove(self.provider)

    def _token_fields(self, fields):
        fields = dict(fields, client_id=self.client_id)
        if self.spec["needs_secret"]:
            fields["client_secret"] = self.client_secret
        return fields

    def connect(self, open_browser=webbrowser.open, timeout=180, cancel=None):
        """Opens the browser sign-in page and waits for the redirect back to this computer."""
        problem = self.credentials_missing()
        if problem:
            raise CloudError(problem)
        verifier, challenge = make_pkce()
        state = secrets.token_urlsafe(16)
        try:
            server = _CallbackServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
        except OSError:
            raise CloudError("Port {} is in use (is another sign-in open?).".format(REDIRECT_PORT)) from None
        try:
            params = {"client_id": self.client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
                      "code_challenge": challenge, "code_challenge_method": "S256", "state": state}
            params.update(self.spec["extra_auth"])
            open_browser(self.spec["auth_url"] + "?" + urllib.parse.urlencode(params))
            server.timeout = 0.5
            deadline = time.time() + timeout
            while server.result is None and time.time() < deadline and not (cancel and cancel()):
                server.handle_request()
        finally:
            server.server_close()
        result = server.result
        if result is None:
            raise CloudError("Sign-in timed out or was cancelled.")
        if result.get("state") != state:
            raise CloudError("Sign-in response didn't match the request. Try again.")
        if "code" not in result:
            raise CloudError("Sign-in was declined: {}".format(result.get("error_description") or result.get("error")))
        tokens = _post_form(self.spec["token_url"], self._token_fields({
            "grant_type": "authorization_code", "code": result["code"],
            "redirect_uri": REDIRECT_URI, "code_verifier": verifier}))
        if not tokens.get("refresh_token"):
            raise CloudError("The service didn't grant lasting access. Try connecting again.")
        record = {"refresh_token": tokens["refresh_token"], "access_token": tokens["access_token"],
                  "expires_at": time.time() + int(tokens.get("expires_in", 3600)), "client_id": self.client_id}
        self.store.put(self.provider, record)
        try:
            record["account"] = self.describe()
            self.store.put(self.provider, record)
        except CloudError:
            pass
        return record.get("account", "")

    def token(self):
        record = self.store.get(self.provider)
        if not record.get("refresh_token") or record.get("client_id") != self.client_id:
            raise AuthError("Not connected to {}. Click Connect first.".format(self.spec["label"]))
        if record.get("expires_at", 0) - 60 > time.time():
            return record["access_token"]
        tokens = _post_form(self.spec["token_url"], self._token_fields({
            "grant_type": "refresh_token", "refresh_token": record["refresh_token"]}))
        record["access_token"] = tokens["access_token"]
        record["expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
        record["refresh_token"] = tokens.get("refresh_token") or record["refresh_token"]
        self.store.put(self.provider, record)
        return record["access_token"]

    def _auth(self, extra=None):
        headers = {"Authorization": "Bearer " + self.token()}
        headers.update(extra or {})
        return headers

    def describe(self):
        if self.provider == "dropbox":
            _, _, raw = _http("POST", "https://api.dropboxapi.com/2/users/get_current_account", self._auth())
            data = json.loads(raw.decode("utf-8"))
            return data.get("email") or data.get("name", {}).get("display_name", "")
        _, _, raw = _http("GET", "https://www.googleapis.com/drive/v3/about?fields=user(displayName,emailAddress)",
                          self._auth())
        user = json.loads(raw.decode("utf-8")).get("user", {})
        return user.get("emailAddress") or user.get("displayName", "")

    def client(self):
        return DropboxClient(self) if self.provider == "dropbox" else DriveClient(self)


class DropboxClient:
    def __init__(self, account):
        self.account = account

    def _content(self, endpoint, arg, data):
        headers = self.account._auth({"Dropbox-API-Arg": json.dumps(arg),
                                      "Content-Type": "application/octet-stream"})
        _, _, raw = _http("POST", "https://content.dropboxapi.com/2/files/" + endpoint, headers, data, timeout=300)
        return json.loads(raw.decode("utf-8") or "{}")

    def upload_file(self, local, parts):
        path = "/" + "/".join(parts)
        commit = {"path": path, "mode": "overwrite", "mute": True}
        size = os.path.getsize(local)
        with open(local, "rb") as handle:
            if size <= SMALL_UPLOAD:
                self._content("upload", commit, handle.read())
                return
            session = self._content("upload_session/start", {"close": False}, handle.read(CHUNK))["session_id"]
            offset = CHUNK
            while size - offset > CHUNK:
                cursor = {"session_id": session, "offset": offset}
                self._content("upload_session/append_v2", {"cursor": cursor, "close": False}, handle.read(CHUNK))
                offset += CHUNK
            self._content("upload_session/finish",
                          {"cursor": {"session_id": session, "offset": offset}, "commit": commit}, handle.read())


class DriveClient:
    def __init__(self, account):
        self.account = account
        self._folders = {}

    def _json(self, method, url, body=None):
        headers = self.account._auth({"Content-Type": "application/json; charset=UTF-8"} if body is not None else None)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        _, _, raw = _http(method, url, headers, data)
        return json.loads(raw.decode("utf-8") or "{}")

    def _find(self, name, parent, folder):
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        query = "name = '{}' and '{}' in parents and trashed = false and mimeType {} '{}'".format(
            escaped, parent, "=" if folder else "!=", DRIVE_FOLDER)
        url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(
            {"q": query, "fields": "files(id)", "pageSize": 1, "spaces": "drive"})
        files = self._json("GET", url).get("files", [])
        return files[0]["id"] if files else None

    def ensure_folder(self, parts):
        parent = "root"
        for index, name in enumerate(parts):
            key = tuple(parts[:index + 1])
            if key not in self._folders:
                folder_id = self._find(name, parent, True)
                if not folder_id:
                    folder_id = self._json("POST", "https://www.googleapis.com/drive/v3/files?fields=id", {
                        "name": name, "mimeType": DRIVE_FOLDER, "parents": [parent]})["id"]
                self._folders[key] = folder_id
            parent = self._folders[key]
        return parent

    def upload_file(self, local, parts):
        parent = self.ensure_folder(parts[:-1])
        name = parts[-1]
        existing = self._find(name, parent, False)
        size = os.path.getsize(local)
        base = "https://www.googleapis.com/upload/drive/v3/files"
        if existing:
            method, url, body = "PATCH", "{}/{}?uploadType=resumable".format(base, existing), {}
        else:
            method, url, body = "POST", base + "?uploadType=resumable", {"name": name, "parents": [parent]}
        headers = self.account._auth({"Content-Type": "application/json; charset=UTF-8",
                                      "X-Upload-Content-Length": str(size)})
        _, response_headers, _ = _http(method, url, headers, json.dumps(body).encode("utf-8"))
        session = response_headers.get("Location")
        if not session:
            raise CloudError("Google Drive didn't start the upload.")
        put_headers = self.account._auth({"Content-Length": str(size)})
        if size:
            with open(local, "rb") as handle:
                _http("PUT", session, put_headers, handle, timeout=600)
        else:
            _http("PUT", session, put_headers, b"")


def upload_backup(client, listing, root_parts, log=None, progress=None):
    """listing: [(source folder, name, [relative files])]. Files land in root_parts/name/... and are overwritten."""
    log = log or (lambda message: None)
    progress = progress or (lambda fraction: None)
    sizes = {}
    for path, _, files in listing:
        for rel in files:
            try:
                sizes[(path, rel)] = os.path.getsize(os.path.join(path, rel))
            except OSError:
                sizes[(path, rel)] = 0
    total = sum(sizes.values()) or 1
    done = uploaded = failed = 0
    for path, name, files in listing:
        for rel in files:
            parts = list(root_parts) + [name] + rel.replace("\\", "/").split("/")
            try:
                client.upload_file(os.path.join(path, rel), parts)
                uploaded += 1
            except AuthError:
                raise
            except (OSError, CloudError) as error:
                failed += 1
                if failed <= 20:
                    log("Warning: couldn't upload {}: {}".format(rel, error))
            done += sizes[(path, rel)]
            progress(done / total)
    progress(1.0)
    log("Uploaded {} files ({} failed)".format(uploaded, failed))
    if failed:
        raise CloudError("{} files couldn't be uploaded; see the log.".format(failed))
    return uploaded
