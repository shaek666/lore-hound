from django.urls import path

from . import views

urlpatterns = [
    path("api/research/", views.research_list_create, name="research-list-create"),
    path("api/research/<int:session_id>/", views.get_session, name="get-session"),
]
