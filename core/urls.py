from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("history/", views.history, name="history"),
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("pair/", views.pair_view, name="pair"),
    path("pick/new/", views.pick_new, name="pick_new"),
    path("api/search/", views.api_search, name="api_search"),
    path("pick/<int:pk>/", views.pick_detail, name="pick_detail"),
    path("pick/<int:pk>/reveal/", views.pick_reveal, name="pick_reveal"),
    path("pick/<int:pk>/episode/<int:ep_id>/toggle/", views.episode_toggle, name="episode_toggle"),
    path("pick/<int:pk>/review/", views.review_create, name="review_create"),
]
