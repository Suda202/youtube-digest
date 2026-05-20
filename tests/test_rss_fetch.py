import unittest
from unittest import mock

import requests

import main


EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>Example Channel</title>
</feed>
"""

VIDEO_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>Example Channel</title>
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <title>Fallback RSS Video</title>
    <published>2099-01-01T00:00:00+00:00</published>
  </entry>
</feed>
"""


class FakeResponse:
    def __init__(self, text="", error=None):
        self.text = text
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error


class RssFetchTests(unittest.TestCase):
    def test_uses_uploads_playlist_rss_when_channel_rss_fails(self):
        channel_id = "UCLKPca3kwwd-B59HNr-_lvA"
        responses = [
            FakeResponse(error=requests.HTTPError("404 Client Error")),
            FakeResponse(text=VIDEO_FEED),
        ]

        with mock.patch.object(main, "RSS_RETRY_ATTEMPTS", 1), \
             mock.patch.object(main.requests, "get", side_effect=responses) as get:
            videos, rss_ok = main.fetch_rss_videos(channel_id)

        self.assertTrue(rss_ok)
        self.assertEqual(videos[0]["video_id"], "abc123")
        self.assertEqual(get.call_count, 2)
        self.assertIn("channel_id=UCLKPca3kwwd-B59HNr-_lvA", get.call_args_list[0].args[0])
        self.assertIn("playlist_id=UULKPca3kwwd-B59HNr-_lvA", get.call_args_list[1].args[0])

    def test_does_not_use_uploads_playlist_when_channel_rss_succeeds_empty(self):
        channel_id = "UCLKPca3kwwd-B59HNr-_lvA"

        with mock.patch.object(main, "RSS_RETRY_ATTEMPTS", 1), \
             mock.patch.object(main.requests, "get", return_value=FakeResponse(text=EMPTY_FEED)) as get:
            videos, rss_ok = main.fetch_rss_videos(channel_id)

        self.assertTrue(rss_ok)
        self.assertEqual(videos, [])
        self.assertEqual(get.call_count, 1)
        self.assertIn("channel_id=UCLKPca3kwwd-B59HNr-_lvA", get.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
