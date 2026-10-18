from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils import timezone


def week_bounds(day=None):
    """Monday..Sunday containing `day`, in the project's TIME_ZONE."""
    day = day or timezone.localdate()
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def space_required(view):
    """Login + a profile + a *complete* couple space. Sets request.profile / request.space / request.partner."""
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        profile = getattr(request.user, "profile", None)
        if profile is None:  # e.g. a createsuperuser account
            logout(request)
            messages.error(request, "That account has no CinePair profile. Sign up with a new username.")
            return redirect("login")
        space = profile.space
        if space is None or space.partner_two_id is None:
            return redirect("pair")
        request.profile, request.space, request.partner = profile, space, space.partner_of(profile)
        return view(request, *args, **kwargs)
    return wrapper
