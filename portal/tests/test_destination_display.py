from types import SimpleNamespace
from django.test import SimpleTestCase


class DestinationDisplayTests(SimpleTestCase):
    def object(self, slug, url, kind='track', external_id=''):
        return SimpleNamespace(service=SimpleNamespace(slug=slug), url=url, object_type=kind, external_id=external_id)

    def test_native_aliases_and_album_links_are_not_repeated(self):
        from portal.destination_display import visible_recording_destinations
        objects = [self.object('spotify', 'https://open.spotify.com/track/1234567890ABCDEFGHIJKL?si=a'),
            self.object('spotify', 'https://open.spotify.com/track/1234567890ABCDEFGHIJKL?si=b'),
            self.object('apple-music', 'https://music.apple.com/us/album/release/123'),
            self.object('apple-music', 'https://music.apple.com/us/album/release/123?i=456')]
        self.assertEqual(list(visible_recording_destinations(objects, 'Song')), [objects[0], objects[3]])

    def test_tiktok_official_sounds_remain_distinct_and_original_audio_is_hidden(self):
        from portal.destination_display import visible_recording_destinations
        objects = [self.object('tiktok', 'https://www.tiktok.com/music/Do-U-Feel-It-7681807683342665745', 'music'),
            self.object('tiktok', 'https://www.tiktok.com/music/Do-U-Feel-It-7675568482579073040', 'music'),
            self.object('tiktok', 'https://www.tiktok.com/music/original-sound-1234567890123456789', 'music')]
        self.assertEqual(list(visible_recording_destinations(objects, 'Do U Feel It?')), objects[:2])
