from django.urls import path
from . import views
from .review import review
from . import user_management
from . import system_accounts
from . import publication
from . import operations, review_queue
from . import insights

app_name='portal'
urlpatterns=[
    path('',views.ArtistLoginView.as_view(),name='login'),
    path('logout/',views.ArtistLogoutView.as_view(),name='logout'),
    path('app/',views.overview,name='overview'),
    path('app/operations/',operations.hub,name='operations'),
    path('app/operations/<slug:key>/',operations.table,name='operation-table'),
    path('app/operations/<slug:key>/new/',operations.edit,name='operation-create'),
    path('app/operations/<slug:key>/<str:object_id>/',operations.detail,name='operation-detail'),
    path('app/operations/<slug:key>/<str:object_id>/edit/',operations.edit,name='operation-edit'),
    path('app/operations/<slug:key>/<str:object_id>/archive/',operations.archive,name='operation-archive'),
    path('app/operations/<slug:key>/<str:object_id>/revoke/',operations.revoke,name='operation-revoke'),
    path('app/operations/<slug:key>/<str:object_id>/remove/',operations.remove_relationship,name='operation-remove'),
    path('app/operations/<slug:key>/<str:object_id>/restore/',operations.restore_relationship,name='operation-restore'),
    path('app/review/',review_queue.review_queue,name='review-queue'),
    path('app/review/issues/<uuid:issue_id>/',review_queue.review_issue,name='review-issue'),
    path('app/review/requests/<int:request_id>/',review_queue.review_request,name='review-request'),
    path('app/users/',user_management.user_list,name='user-list'),
    path('app/users/new/',user_management.user_edit,name='user-create'),
    path('app/users/<int:user_id>/edit/',user_management.user_edit,name='user-edit'),
    path('app/users/<int:user_id>/system-access/',system_accounts.system_account,name='system-account'),
    path('app/users/<int:user_id>/reset-password/',system_accounts.reset_account_password,name='reset-account-password'),
    path('app/artists/<uuid:artist_id>/',views.artist_detail,name='artist'),
    path('app/artists/<uuid:artist_id>/insights/',insights.insights,name='insights'),
    path('app/artists/<uuid:artist_id>/insights/<uuid:source_id>/',insights.detail,name='insight-detail'),
    path('app/artists/<uuid:artist_id>/publish/',publication.publish,name='publish'),
    path('app/artists/<uuid:artist_id>/publication-settings/',publication.publication_settings,name='publication-settings'),
    path('app/artists/<uuid:artist_id>/catalog/',views.catalog,name='catalog'),
    path('app/artists/<uuid:artist_id>/review/<uuid:website_id>/',review,name='review'),
    path('app/artists/<uuid:artist_id>/request-review/',views.request_review,name='request-review'),
    path('app/artists/<uuid:artist_id>/recordings/<uuid:object_id>/',views.recording_detail,name='recording'),
    path('app/artists/<uuid:artist_id>/releases/<uuid:object_id>/',views.release_detail,name='release'),
    path('app/artists/<uuid:artist_id>/assets/<uuid:object_id>/<str:action>/',views.asset_media,name='asset-media'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/',views.resource_list,name='resources'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/new/',views.edit_resource,name='create'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/<str:object_id>/',views.resource_detail,name='detail'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/<str:object_id>/edit/',views.edit_resource,name='edit'),
    path('app/artists/<uuid:artist_id>/data/<slug:kind>/<str:object_id>/delete/',views.delete_resource,name='delete'),
]
