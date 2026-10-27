import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cloud_storage as cs


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class CloudStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = cs.TokenStore(os.path.join(self.tmp.name, "tokens.json"))

    def test_pkce_challenge_matches_verifier(self):
        import base64, hashlib
        verifier, challenge = cs.make_pkce()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)

    def test_split_remote_path(self):
        self.assertEqual(cs.split_remote_path("/Backups\\Projects/"), ["Backups", "Projects"])
        self.assertEqual(cs.split_remote_path(""), [])

    def test_connect_flow_stores_tokens(self):
        account = cs.Account("dropbox", "KEY123", store=self.store)
        seen = {}

        def fake_browser(url):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            seen["query"] = query

            def hit():
                target = "{}?code=abc&state={}".format(cs.REDIRECT_URI, query["state"][0])
                urllib.request.urlopen(target, timeout=5).read()
            threading.Thread(target=hit).start()

        def fake_post(url, fields):
            seen["fields"] = fields
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

        with mock.patch.object(cs, "_post_form", fake_post), \
                mock.patch.object(cs.Account, "describe", lambda self: "me@example.com"):
            who = account.connect(open_browser=fake_browser, timeout=10)
        self.assertEqual(who, "me@example.com")
        self.assertEqual(seen["query"]["client_id"], ["KEY123"])
        self.assertEqual(seen["query"]["code_challenge_method"], ["S256"])
        self.assertEqual(seen["fields"]["code"], "abc")
        self.assertIn("code_verifier", seen["fields"])
        self.assertTrue(account.is_connected())
        self.assertEqual(account.connected_as(), "me@example.com")
        account.disconnect()
        self.assertFalse(account.is_connected())

    def test_connect_rejects_wrong_state(self):
        account = cs.Account("dropbox", "KEY", store=self.store)

        def fake_browser(url):
            threading.Thread(target=lambda: urllib.request.urlopen(
                cs.REDIRECT_URI + "?code=abc&state=wrong", timeout=5).read()).start()

        with self.assertRaises(cs.CloudError):
            account.connect(open_browser=fake_browser, timeout=10)

    def test_google_needs_secret(self):
        self.assertTrue(cs.Account("google_drive", "id", store=self.store).credentials_missing())
        self.assertFalse(cs.Account("google_drive", "id", "secret", store=self.store).credentials_missing())

    def test_token_refreshes_when_expired(self):
        self.store.put("dropbox", {"refresh_token": "RT", "access_token": "old", "expires_at": 0, "client_id": "K"})
        account = cs.Account("dropbox", "K", store=self.store)
        with mock.patch.object(cs, "_post_form", lambda url, f: {"access_token": "new", "expires_in": 100}):
            self.assertEqual(account.token(), "new")
        self.assertEqual(self.store.get("dropbox")["refresh_token"], "RT")

    def test_token_requires_matching_client(self):
        self.store.put("dropbox", {"refresh_token": "RT", "access_token": "a", "expires_at": 9e12, "client_id": "K"})
        with self.assertRaises(cs.AuthError):
            cs.Account("dropbox", "OTHER", store=self.store).token()

    def make_files(self):
        root = os.path.join(self.tmp.name, "Proj")
        os.makedirs(os.path.join(root, "sub"))
        for rel, text in (("a.txt", "hello"), (os.path.join("sub", "b.txt"), "world")):
            with open(os.path.join(root, rel), "w") as handle:
                handle.write(text)
        return root, [(root, "Proj", ["a.txt", os.path.join("sub", "b.txt")])]

    def test_dropbox_upload_paths_and_args(self):
        self.store.put("dropbox", {"refresh_token": "RT", "access_token": "AT", "expires_at": 9e12, "client_id": "K"})
        client = cs.Account("dropbox", "K", store=self.store).client()
        calls = []

        def fake_http(method, url, headers=None, data=None, timeout=60, retries=4):
            calls.append((url, json.loads(headers["Dropbox-API-Arg"]), data))
            return 200, FakeHeaders(), b"{}"

        _, listing = self.make_files()
        progress = []
        with mock.patch.object(cs, "_http", fake_http):
            n = cs.upload_backup(client, listing, ["Backups", "Proj Backup"], progress=progress.append)
        self.assertEqual(n, 2)
        self.assertEqual(calls[0][1]["path"], "/Backups/Proj Backup/Proj/a.txt")
        self.assertEqual(calls[1][1]["path"], "/Backups/Proj Backup/Proj/sub/b.txt")
        self.assertEqual(calls[0][1]["mode"], "overwrite")
        self.assertEqual(calls[0][2], b"hello")
        self.assertEqual(progress[-1], 1.0)

    def test_dropbox_large_file_uses_session(self):
        self.store.put("dropbox", {"refresh_token": "RT", "access_token": "AT", "expires_at": 9e12, "client_id": "K"})
        client = cs.Account("dropbox", "K", store=self.store).client()
        path = os.path.join(self.tmp.name, "big.bin")
        with open(path, "wb") as handle:
            handle.write(b"x" * 100)
        steps = []

        def fake_http(method, url, headers=None, data=None, timeout=60, retries=4):
            steps.append((url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1], len(data)))
            return 200, FakeHeaders(), b'{"session_id": "S"}'

        with mock.patch.object(cs, "SMALL_UPLOAD", 10), mock.patch.object(cs, "CHUNK", 40), \
                mock.patch.object(cs, "_http", fake_http):
            client.upload_file(path, ["big.bin"])
        self.assertEqual([s[0] for s in steps],
                         ["upload_session/start", "upload_session/append_v2", "upload_session/finish"])
        self.assertEqual(sum(s[1] for s in steps), 100)

    def test_drive_creates_folders_and_uploads(self):
        self.store.put("google_drive", {"refresh_token": "RT", "access_token": "AT", "expires_at": 9e12, "client_id": "I"})
        client = cs.Account("google_drive", "I", "S", store=self.store).client()
        created, uploads = [], []

        def fake_http(method, url, headers=None, data=None, timeout=60, retries=4):
            if method == "GET":
                return 200, FakeHeaders(), b'{"files": []}'
            if method == "POST" and "uploadType" not in url:
                body = json.loads(data)
                created.append((body["name"], body["parents"][0]))
                return 200, FakeHeaders(), json.dumps({"id": "id_" + body["name"]}).encode()
            if "uploadType=resumable" in url:
                return 200, FakeHeaders(Location="https://upload/session"), b""
            uploads.append(url)
            return 200, FakeHeaders(), b"{}"

        _, listing = self.make_files()
        with mock.patch.object(cs, "_http", fake_http):
            cs.upload_backup(client, listing, ["Backups"])
        self.assertEqual(created, [("Backups", "root"), ("Proj", "id_Backups"), ("sub", "id_Proj")])
        self.assertEqual(len(uploads), 2)

    def test_auth_error_stops_upload(self):
        class Client:
            calls = 0

            def upload_file(self, local, parts):
                Client.calls += 1
                raise cs.AuthError("expired")

        _, listing = self.make_files()
        with self.assertRaises(cs.AuthError):
            cs.upload_backup(Client(), listing, ["B"])
        self.assertEqual(Client.calls, 1)

    def test_failed_files_raise_after_trying_all(self):
        class Client:
            calls = 0

            def upload_file(self, local, parts):
                Client.calls += 1
                raise cs.CloudError("nope")

        _, listing = self.make_files()
        with self.assertRaises(cs.CloudError):
            cs.upload_backup(Client(), listing, ["B"])
        self.assertEqual(Client.calls, 2)


if __name__ == "__main__":
    unittest.main()
