"""Public TikTok collection contracts; all requests use local fixtures."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from provenance.collectors.http import CollectionError
from provenance.collectors.tiktok import collect_tiktok


PROFILE = {"id": "42", "username": "artist", "is_private": False,
           "stats": {"follower_count": 0}}
VIDEO_FIELDS = (
    "id link type description title language created_at location_created is_ad is_pinned "
    "is_private is_ai_generated is_original is_official is_duet_enabled is_stitch_enabled "
    "is_share_enabled playlist_id stats author author_stats music video images cover hashtags "
    "mentions location effects text_stickers anchors suggested_searches"
).split()


def video(video_id="91", *, music_id="7", author=None, full=False):
    result = dict.fromkeys(VIDEO_FIELDS) if full else {}
    result.update(id=video_id, is_private=False, author=author or deepcopy(PROFILE),
                  stats=dict.fromkeys(("play_count", "like_count", "comment_count", "share_count",
                                       "save_count", "repost_count"), 0),
                  music={"id": music_id} if music_id else None)
    if full:
        result["music"] = None
        result["video"] = None
        result["author"] = {**dict.fromkeys(("id", "sec_uid", "username", "nickname", "link",
                                           "profile_picture", "is_verified", "is_private", "biography")),
                            **result["author"]}
        result["author_stats"] = dict.fromkeys(("follower_count", "following_count", "like_count",
                                               "video_count", "friend_count", "liked_video_count"), 0)
    return result


class FixtureFetch:
    def __init__(self, responses=None):
        self.responses = {
            "/users/profile": deepcopy(PROFILE),
            "/users/videos": {"videos": [], "next_cursor": None, "has_more": False},
            "/users/playlists": {"playlists": [], "next_cursor": None, "has_more": False},
            **(responses or {}),
        }
        self.calls = []

    def __call__(self, path, **params):
        self.calls.append((path, params.copy()))
        result = self.responses.get((path, params.get("cursor")), self.responses.get(path))
        if callable(result):
            result = result(params)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise AssertionError(f"Unexpected request {path}: {params}")
        return deepcopy(result)


class TikTokCollectorTests(unittest.TestCase):
    locator = "https://www.tiktok.com/@artist/"

    def collect(self, fetch, **kwargs):
        return list(collect_tiktok(self.locator, fetch, **kwargs))

    def test_profile_zero_counters_and_empty_collections(self):
        fetch = FixtureFetch()
        rows = self.collect(fetch)
        self.assertEqual(rows[0].endpoint, "tiktok-user")
        self.assertEqual(rows[0].payload["stats"]["follower_count"], 0)
        self.assertEqual({r.endpoint for r in rows},
                         {"tiktok-user", "tiktok-users-videos", "tiktok-users-playlists"})
        self.assertFalse(any(r.incomplete for r in rows))

    def test_private_missing_privacy_and_wrong_profile_stop_before_catalog(self):
        for change, error in (({"is_private": True}, "private_profile"),
                              ({"is_private": None}, "missing_privacy_status"),
                              ({"username": "another"}, "profile_identity_mismatch")):
            with self.subTest(change=change):
                fetch = FixtureFetch({"/users/profile": {**PROFILE, **change}})
                with self.assertRaisesRegex(CollectionError, error):
                    self.collect(fetch)
                self.assertEqual(len(fetch.calls), 1)

    def test_partial_own_video_keeps_zero_counts_on_detail_failure(self):
        fetch = FixtureFetch({"/users/videos": {"videos": [video(music_id=None)]},
                              "/videos/details": CollectionError("http_502")})
        rows = self.collect(fetch)
        self.assertEqual([r.payload["stats"]["play_count"] for r in rows if r.kind == "video"], [0])
        self.assertTrue(any(r.endpoint == "tiktok-video-unavailable" and r.incomplete for r in rows))

    def test_full_video_skips_duplicate_details_and_walks_all_pages(self):
        fetch = FixtureFetch({"/users/videos": {"videos": [video(full=True)], "next_cursor": "next", "has_more": True},
                              ("/users/videos", "next"): {"videos": [video("92", full=True)]}})
        rows = self.collect(fetch)
        self.assertEqual([r.external_id for r in rows if r.kind == "video"], ["91", "92"])
        self.assertNotIn("/videos/details", [p for p, _ in fetch.calls])
        self.assertEqual(fetch.calls[2], ("/users/videos", {"user": "artist", "sort": "latest", "cursor": "next"}))

    def test_complete_stats_without_full_metadata_still_fetches_detail(self):
        fetch = FixtureFetch({"/users/videos": {"videos": [video(music_id=None)]},
                              "/videos/details": {**video(music_id=None), "description": "Full detail"}})
        rows = self.collect(fetch)
        self.assertTrue(any(r.kind == "video" and r.payload.get("description") == "Full detail" for r in rows))

    def test_foreign_author_private_video_and_conflicting_author_are_excluded(self):
        foreign = video("1", author={"id": "88", "username": "another"})
        collision = video("2", author={"id": "42", "username": "another"})
        private = {**video("3"), "is_private": True}
        fetch = FixtureFetch({"/users/videos": {"videos": [foreign, collision, private]}})
        rows = self.collect(fetch)
        self.assertFalse(any(r.kind in {"video", "music", "usage_video"} for r in rows))
        self.assertFalse(any(p in {"/videos/details", "/music/details"} for p, _ in fetch.calls))

    def test_seeded_music_fan_usage_is_separate_and_cursor_paginated(self):
        fetch = FixtureFetch({"/music/details": {"id": "7", "video_count": 0, "is_private": False},
                              "/music/videos": {"music_id": "7", "videos": [video(author={"id": "88"})], "next_cursor": "n"},
                              ("/music/videos", "n"): {"music_id": "7", "videos": [video("92", author={"id": "89"}), video("93")]}})
        rows = self.collect(fetch, music_ids=("7", "7"))
        music = [r for r in rows if r.kind == "music"]
        self.assertEqual(len(music), 1)
        self.assertEqual(music[0].payload["_collection"]["scope"], "artist_music")
        usages = [r for r in rows if r.kind == "usage_video"]
        self.assertEqual([r.external_id for r in usages], ["91", "92"])
        self.assertTrue(all(r.endpoint == "tiktok-usage-video" and r.payload["_collection"]["music_id"] == "7" for r in usages))
        self.assertFalse(any(r.kind == "video" for r in rows))
        self.assertEqual(sum(p == "/users/profile" for p, _ in fetch.calls), 1)
        self.assertEqual(sum(p == "/music/details" for p, _ in fetch.calls), 1)

    def test_discovered_unowned_music_gets_metadata_without_usage_or_music_metrics(self):
        fetch = FixtureFetch({"/users/videos": {"videos": [video()]},
                              "/videos/details": video(),
                              "/music/details": {"id": "7", "title": "Artist song", "author_name": "artist", "video_count": 99}})
        rows = self.collect(fetch)
        self.assertFalse(any(r.kind in {"music", "usage_video"} for r in rows))
        self.assertTrue(any(r.kind == "collection" and r.endpoint == "tiktok-music-unconfirmed" for r in rows))
        self.assertNotIn("/music/videos", [p for p, _ in fetch.calls])

    def test_discovered_music_identity_or_catalog_evidence_allows_usage(self):
        for author, predicate in (({"id": "42"}, None), (None, lambda payload: payload.get("id") == "7")):
            with self.subTest(author=author):
                fetch = FixtureFetch({"/users/videos": {"videos": [video()]},
                                      "/videos/details": video(),
                                      "/music/details": {"id": "7", "author": author},
                                      "/music/videos": {"music_id": "7", "videos": []}})
                rows = self.collect(fetch, is_artist_music=predicate)
                self.assertTrue(any(r.kind == "music" for r in rows))
                self.assertIn("/music/videos", [p for p, _ in fetch.calls])

    def test_private_seeded_music_and_private_usage_never_become_fan_tracking(self):
        fetch = FixtureFetch({"/music/details": {"id": "7", "is_private": True}})
        rows = self.collect(fetch, music_ids=("7",))
        self.assertFalse(any(r.kind in {"music", "usage_video"} for r in rows))
        self.assertNotIn("/music/videos", [p for p, _ in fetch.calls])
        fetch = FixtureFetch({"/music/details": {"id": "7", "is_private": False},
                              "/music/videos": {"music_id": "7", "videos": [{**video(), "is_private": True}, video("92", author={"id": "99", "is_private": True})]}})
        self.assertFalse(any(r.kind == "usage_video" for r in self.collect(fetch, music_ids=("7",))))

    def test_music_identity_contradictions_mark_incomplete(self):
        for music_detail, page in (({"id": "8"}, None),
                                   ({"id": "7"}, {"music_id": "8", "videos": [video()]}),
                                   ({"id": "7"}, {"music_id": "7", "videos": [video(music_id="8")]})):
            with self.subTest(detail=music_detail, page=page):
                fetch = FixtureFetch({"/music/details": music_detail, "/music/videos": page})
                rows = self.collect(fetch, music_ids=("7",))
                self.assertFalse(any(r.kind == "usage_video" for r in rows))
                self.assertTrue(any(r.incomplete for r in rows))

    def test_playlist_metadata_and_own_videos_only(self):
        playlist = {"id": "5", "name": "Artist series", "creator": PROFILE}
        fetch = FixtureFetch({"/users/playlists": {"playlists": [playlist]},
                              "/playlists/details": playlist,
                              "/playlists/videos": {"playlist_id": "5", "videos": [video(full=True), video("92", author={"id": "99"})]}})
        rows = self.collect(fetch)
        self.assertEqual([r.external_id for r in rows if r.kind == "video"], ["91"])
        self.assertTrue(any(r.endpoint == "tiktok-playlists-details" and r.kind == "collection" for r in rows))

    def test_invalid_collection_and_repeated_cursor_preserve_independent_branch(self):
        for response in ({"videos": None}, {"videos": [], "next_cursor": "same"}):
            with self.subTest(response=response):
                rows = self.collect(FixtureFetch({"/users/videos": response}))
                self.assertTrue(any(r.incomplete for r in rows))
                self.assertTrue(any(r.endpoint == "tiktok-users-playlists" for r in rows))

    def test_retry_same_cursor_and_budget_exhaustion_propagates(self):
        count = 0
        def page(params):
            nonlocal count
            count += 1
            if count == 1:
                raise CollectionError("http_502")
            return {"videos": []}
        fetch = FixtureFetch({"/users/videos": page})
        with patch("provenance.collectors.providers.time.sleep"):
            self.collect(fetch)
        self.assertEqual(fetch.calls[1], fetch.calls[2])
        with self.assertRaisesRegex(CollectionError, "job_request_limit"):
            self.collect(FixtureFetch({"/users/videos": CollectionError("job_request_limit")}))

    def test_sparse_detail_does_not_erase_valid_list_counts(self):
        sparse = video(music_id=None)
        sparse["stats"] = {"like_count": None}
        fetch = FixtureFetch({"/users/videos": {"videos": [video(music_id=None)]},
                              "/videos/details": sparse})
        rows = self.collect(fetch)
        details = [r for r in rows if r.kind == "video" and r.payload["_collection"]["endpoint"] == "tiktok-videos-details"]
        self.assertEqual(details[0].payload["stats"]["like_count"], 0)
        self.assertEqual(details[0].payload["stats"]["play_count"], 0)

    def test_wrong_detail_video_identity_retains_original_evidence(self):
        for change in ({"id": "92"}, {"author": {"id": "99"}}, {"is_private": True}):
            with self.subTest(change=change):
                fetch = FixtureFetch({"/users/videos": {"videos": [video(music_id=None)]},
                                      "/videos/details": {**video(music_id=None), **change}})
                rows = self.collect(fetch)
                self.assertEqual([r.external_id for r in rows if r.kind == "video"], ["91"])
                self.assertTrue(any(r.incomplete for r in rows))

    def test_missing_music_feed_identity_is_incomplete(self):
        fetch = FixtureFetch({"/music/details": {"id": "7"},
                              "/music/videos": {"videos": [video(author={"id": "99"})]}})
        rows = self.collect(fetch, music_ids=("7",))
        self.assertFalse(any(r.kind == "usage_video" for r in rows))
        self.assertTrue(any(r.incomplete for r in rows))

    def test_unknown_or_partially_matching_author_is_not_counted_as_fan(self):
        fetch = FixtureFetch({"/music/details": {"id": "7"},
                              "/music/videos": {"music_id": "7", "videos": [
                                  video("1", author={"id": "42", "username": "another"}),
                                  {**video("2"), "author": None},
                                  video("3", author={"nickname": "A person"})]}})
        self.assertFalse(any(r.kind == "usage_video" for r in self.collect(fetch, music_ids=("7",))))

    def test_user_page_identity_mismatch_is_incomplete_and_stops_branch(self):
        fetch = FixtureFetch({"/users/videos": {"user": {"id": "99"}, "videos": [video()]}})
        rows = self.collect(fetch)
        self.assertFalse(any(r.kind == "video" for r in rows))
        self.assertTrue(any(r.incomplete for r in rows))
        self.assertTrue(any(r.endpoint == "tiktok-users-playlists" for r in rows))

    def test_full_top_level_metadata_with_partial_author_still_needs_details(self):
        payload = video(full=True)
        payload["author"] = {"id": "42", "username": "artist"}
        fetch = FixtureFetch({"/users/videos": {"videos": [payload]},
                              "/videos/details": {**payload, "description": "Enriched"}})
        self.collect(fetch)
        self.assertIn("/videos/details", [p for p, _ in fetch.calls])
