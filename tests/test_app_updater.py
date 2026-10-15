import io
import json
import unittest
from unittest.mock import patch

import certifi

import app_updater


class AppUpdaterTests(unittest.TestCase):
    def test_opens_https_requests_with_bundled_ca_certificates(self):
        request = object()
        context = object()
        response = object()

        with (
            patch("app_updater.ssl.create_default_context", return_value=context) as create_context,
            patch("app_updater.urllib.request.urlopen", return_value=response) as urlopen,
        ):
            result = app_updater._open_url(request, timeout=30)

        self.assertIs(result, response)
        create_context.assert_called_once_with(cafile=certifi.where())
        urlopen.assert_called_once_with(request, timeout=30, context=context)

    def test_update_check_uses_the_bundled_ca_context(self):
        context = object()
        response = io.BytesIO(json.dumps({
            "tag_name": "v9.8.7",
            "assets": [{
                "name": "AutoAE-macos.zip",
                "size": 123,
                "browser_download_url": (
                    "https://github.com/CWrath/autoae/releases/download/v9.8.7/AutoAE-macos.zip"
                ),
            }],
        }).encode("utf-8"))

        with (
            patch("app_updater.ssl.create_default_context", return_value=context) as create_context,
            patch("app_updater.urllib.request.urlopen", return_value=response) as urlopen,
        ):
            release = app_updater.check_for_update(current_version="1.0.7", system="darwin")

        self.assertEqual(release["version"], "9.8.7")
        self.assertEqual(release["asset_name"], "AutoAE-macos.zip")
        create_context.assert_called_once_with(cafile=certifi.where())
        self.assertIs(urlopen.call_args.kwargs["context"], context)


if __name__ == "__main__":
    unittest.main()
