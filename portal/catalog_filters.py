"""Catalog filters over authorized, active relationships only."""
from urllib.parse import urlencode
from django.db.models import Count,F,Q
from django.db.models.functions import Lower
from catalog.models import Genre
from .querysets import scoped


SONG_TYPES = (('solo','Solo/Originals'),('collaborations','Collaborations'),('remixes','Remixes'))
PERFORMER_ROLES = ('primary','featured','collaborator','guest','vocalist','performer','remixer')


def catalog_selection(user,artist,params):
    kind = 'releases' if params.get('kind')=='releases' else 'recordings'
    rows = scoped(user,artist,kind)
    query = params.get('q','')[:200]
    status = params.get('status','')
    status = status if status in ('draft','review','approved') else ''
    song_type = params.get('song_type','') if kind=='recordings' else ''
    song_type = song_type if song_type in dict(SONG_TYPES) else ''
    genre = params.get('genre','')[:100]
    genre_links = scoped(user,artist,'recording-genres' if kind=='recordings' else 'release-genres')
    genre_ids = genre_links.values('genre_id')
    if kind=='releases':
        tracks = scoped(user,artist,'tracks')
        track_genres = scoped(user,artist,'recording-genres').filter(recording_id__in=tracks.values('recording_id'))
        genre_ids = list(genre_ids.values_list('genre_id',flat=True))+list(track_genres.values_list('genre_id',flat=True))
    genres = list(Genre.objects.filter(pk__in=genre_ids).order_by(Lower('name'),'pk'))
    if query:
        stem = 'recording' if kind=='recordings' else 'release'
        identifiers = scoped(user,artist,stem+'-identifiers').filter(value__icontains=query)
        rows = rows.filter(Q(title__icontains=query)|Q(pk__in=identifiers.values(stem+'_id')))
    if status:
        rows = rows.filter(status=status)
    if song_type:
        credits = scoped(user,artist,'recording-credits').filter(artist__organization=artist.organization,artist__archived_at__isnull=True).annotate(normalized_role=Lower('role'))
        performers = credits.filter(normalized_role__in=PERFORMER_ROLES)
        multiple = performers.values('recording_id').annotate(artist_count=Count('artist_id',distinct=True)).filter(artist_count__gt=1)
        featured = performers.filter(normalized_role__in=('featured','collaborator','guest'))
        relations = scoped(user,artist,'recording-relations').filter(relation_type__in=('remix','remix_of'))
        remix = Q(recording_type__iexact='remix')|Q(pk__in=relations.values('from_recording_id'))
        collaboration = Q(recording_type__iexact='collaboration')|Q(pk__in=multiple.values('recording_id'))|Q(pk__in=featured.values('recording_id'))
        if song_type=='remixes':
            rows = rows.filter(remix)
        elif song_type=='collaborations':
            rows = rows.exclude(remix).filter(collaboration)
        else:
            rows = rows.exclude(remix).exclude(collaboration)
    if genre:
        selected = Q(pk__in=genre_links.filter(genre__slug=genre).values('recording_id' if kind=='recordings' else 'release_id'))
        if kind=='releases':
            selected |= Q(pk__in=tracks.filter(recording_id__in=track_genres.filter(genre__slug=genre).values('recording_id')).values('release_id'))
        rows = rows.filter(selected)
    date_field = 'original_release_date' if kind=='recordings' else 'release_date'
    rows = rows.order_by(F(date_field).desc(nulls_last=True),'title','pk')
    state = {key:value for key,value in dict(kind=kind,q=query,status=status,song_type=song_type,genre=genre).items() if value}

    def url(**changes):
        return '?'+urlencode({key:value for key,value in {**state,**changes}.items() if value})

    return rows,dict(kind=kind,query=query,status_filter=status,song_type=song_type,genre_filter=genre,
                     genre_options=genres,selected_genre_missing=bool(genre) and not any(row.slug==genre for row in genres),song_types=SONG_TYPES,filter_query=urlencode(state),
                     songs_url=url(kind='recordings'),releases_url=url(kind='releases',song_type=''),
                     clear_search_url=url(q=''),reset_url='?kind='+kind)
