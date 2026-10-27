"""Scoped, read-only audio profiles. Missing measurements never become zero."""
import math

AUDIO_FEATURES = (("acousticness", "Acousticness"), ("danceability", "Danceability"),
    ("energy", "Energy"), ("instrumentalness", "Instrumentalness"), ("liveness", "Liveness"),
    ("speechiness", "Speechiness"), ("valence", "Valence"))
KEYS = ('C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B')


def number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value if (low is None or value >= low) and (high is None or value <= high) else None


def audio_profile(payloads):
    payloads = list(payloads)
    features, points, axes = [], [], []
    for index, (code, label) in enumerate(AUDIO_FEATURES):
        values = [value for payload in payloads if (value := number(payload.get(code), 0, 1)) is not None]
        average = sum(values) / len(values) if values else None
        angle = -math.pi / 2 + index * 2 * math.pi / 7
        x, y = math.cos(angle), math.sin(angle)
        axes.append({'x': f'{180 + x * 103:.2f}', 'y': f'{162 + y * 103:.2f}',
            'label_x': f'{180 + x * 128:.2f}', 'label_y': f'{162 + y * 125:.2f}',
            'anchor': 'middle' if abs(x) < .1 else 'start' if x > 0 else 'end', 'label': label})
        if average is not None:
            points.append(f'{180 + x * 103 * average:.2f},{162 + y * 103 * average:.2f}')
        features.append({'code': code, 'label': label, 'value': average, 'count': len(values)})
    rings = [' '.join(f'{180 + math.cos(-math.pi / 2 + i * 2 * math.pi / 7) * 103 * scale:.2f},{162 + math.sin(-math.pi / 2 + i * 2 * math.pi / 7) * 103 * scale:.2f}' for i in range(7)) for scale in (.25, .5, .75, 1)]
    loudness = [v for p in payloads if (v := number(p.get('loudness'), -100, 20)) is not None]
    body = payloads[0] if len(payloads) == 1 else {}
    key, mode = body.get('key'), body.get('mode')
    key_text = f'{KEYS[key]} {"Major" if mode else "Minor"}' if type(key) is int and 0 <= key <= 11 and type(mode) is int and mode in (0, 1) else 'Unknown' if key == -1 else 'Unavailable'
    tempo = number(body.get('tempo'), 0, 1000)
    signature = body.get('time_signature')
    length = number(body.get('duration_ms'), 0)
    from .catalog_display import duration
    stats = [('Key', key_text), ('Tempo', f'{tempo:g} BPM' if tempo is not None else 'Unavailable'),
        ('Time signature', f'{signature}/4' if type(signature) is int and 1 <= signature <= 16 else 'Unavailable'),
        ('Duration', duration(int(length)) if length is not None else 'Unavailable')]
    return {'features': features, 'axes': axes, 'rings': rings, 'polygon': ' '.join(points) if len(points) == 7 else '',
        'coverage': sum(any(number(p.get(code), 0, 1) is not None for code, _ in AUDIO_FEATURES) for p in payloads),
        'loudness': {'average': sum(loudness) / len(loudness), 'min': min(loudness), 'max': max(loudness), 'count': len(loudness)} if loudness else None,
        'stats': stats, 'aggregate': len(payloads) != 1}


def recording_audio(artist, recording):
    from provenance.collectors.audio_features import latest_audio_features
    payload = latest_audio_features(recording, artist=artist)
    profile = audio_profile([payload] if payload else [])
    profile['aggregate'] = False
    profile['duration_ms'] = payload.get('duration_ms') if payload else None
    return profile


def artist_audio(user, artist):
    from .querysets import scoped
    from provenance.collectors.audio_features import latest_audio_features_for_recordings
    recordings = list(scoped(user, artist, 'recordings').distinct())
    payloads = latest_audio_features_for_recordings(recordings, artist=artist).values()
    profile = audio_profile(payloads)
    profile.update(aggregate=True, total_songs=len(recordings))
    return profile
