from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from . import tmdb
from .forms import AVATAR_COLORS, LoginForm, ReviewForm, SignupForm
from .models import CoupleSpace, EpisodeProgress, Profile, Review, WeeklyPick
from .utils import space_required, week_bounds

BACKEND = "django.contrib.auth.backends.ModelBackend"


# ---------- Auth ----------

def signup_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None, initial={"avatar_color": "indigo"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=d["username"])
                user.set_unusable_password()  # the PIN lives on Profile, not on the Django password
                user.save()
                profile = Profile(user=user, display_name=d["display_name"].strip(), avatar_color=d["avatar_color"])
                profile.set_pin(d["pin"])
                profile.save()
        except IntegrityError:
            form.add_error("username", "That username is taken.")
        else:
            login(request, user, backend=BACKEND)
            return redirect("pair")
    return render(request, "core/auth/signup.html", {"form": form, "colors": AVATAR_COLORS})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        profile = (
            Profile.objects.select_related("user")
            .filter(user__username__iexact=form.cleaned_data["username"].strip())
            .first()
        )
        if profile and profile.is_locked():
            form.add_error(None, "Too many wrong PINs. Try again in 15 minutes.")
        elif profile and profile.check_pin(form.cleaned_data["pin"]):
            profile.reset_failures()
            login(request, profile.user, backend=BACKEND)
            nxt = request.GET.get("next", "")
            if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
                return redirect(nxt)
            return redirect("dashboard")
        else:
            if profile:
                profile.register_failure()
            form.add_error(None, "Wrong username or PIN.")
    return render(request, "core/auth/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def pair_view(request):
    profile = getattr(request.user, "profile", None)
    if profile is None:
        logout(request)
        return redirect("login")
    space = profile.space
    if space and space.partner_two_id:
        return redirect("dashboard")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create" and space is None:
            for _ in range(5):
                try:
                    with transaction.atomic():
                        CoupleSpace.objects.create(partner_one=profile, invite_code=CoupleSpace.new_invite_code())
                    break
                except IntegrityError:
                    continue
            return redirect("pair")
        if action == "join":
            code = request.POST.get("code", "").strip().upper()
            target = CoupleSpace.objects.filter(invite_code=code, partner_two__isnull=True).exclude(partner_one=profile).first()
            if not code or target is None:
                messages.error(request, "That code doesn't match an open space.")
            else:
                # Atomic claim: only succeeds if nobody else grabbed the second seat first.
                claimed = CoupleSpace.objects.filter(pk=target.pk, partner_two__isnull=True).update(partner_two=profile, invite_code=None)
                if claimed:
                    if space is not None:  # drop our own unused pending space
                        space.delete()
                    return redirect("dashboard")
                messages.error(request, "Someone else just claimed that space.")
    return render(request, "core/pair.html", {"space": space})


# ---------- Dashboard / history ----------

@space_required
def dashboard(request):
    start, end = week_bounds()
    picks = WeeklyPick.objects.filter(couple_space=request.space, week_start_date=start).select_related("picker", "assignee")
    ctx = {
        "week_start": start, "week_end": end,
        "for_me": picks.filter(assignee=request.profile).first(),
        "by_me": picks.filter(picker=request.profile).first(),
        "partner": request.partner,
    }
    return render(request, "core/dashboard.html", ctx)


@space_required
def history(request):
    today = timezone.localdate()
    picks = (
        WeeklyPick.objects.filter(couple_space=request.space)
        .filter(Q(status="completed") | Q(week_end_date__lt=today))
        .select_related("picker", "assignee").prefetch_related("reviews")
    )
    rows, hits = [], {request.profile.pk: [], request.partner.pk: []}
    for p in picks:
        revs = {r.reviewer_id: r for r in p.reviews.all()}
        mine, theirs = revs.get(request.profile.pk), revs.get(request.partner.pk)
        if mine and theirs:
            assignee_review = mine if p.assignee_id == request.profile.pk else theirs
            hits[p.picker_id].append(float(assignee_review.rating))
        rows.append({
            "pick": p, "mine": mine,
            "theirs": theirs if mine else None,        # spoiler rule: only after you've rated
            "theirs_waiting": bool(theirs and not mine),
        })
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    scoreboard = {
        "mine": avg(hits[request.profile.pk]), "mine_n": len(hits[request.profile.pk]),
        "theirs": avg(hits[request.partner.pk]), "theirs_n": len(hits[request.partner.pk]),
    }
    return render(request, "core/history.html", {"rows": rows, "scoreboard": scoreboard, "partner": request.partner})


# ---------- Picks ----------

@space_required
@require_GET
def api_search(request):
    try:
        return JsonResponse({"results": tmdb.search(request.GET.get("q", ""))})
    except tmdb.TMDBError as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@space_required
def pick_new(request):
    start, end = week_bounds()
    if WeeklyPick.objects.filter(couple_space=request.space, picker=request.profile, week_start_date=start).exists():
        messages.info(request, "You've already picked for this week.")
        return redirect("dashboard")

    error = None
    if request.method == "POST":
        try:
            tmdb_id = int(request.POST.get("tmdb_id", ""))
            media_type = request.POST.get("media_type", "")
            meta = tmdb.details(media_type, tmdb_id)  # server-side truth, not form fields
            if meta["total_episodes"] > settings.MAX_EPISODES_PER_PICK:
                error = f"{meta['title']} has {meta['total_episodes']} episodes. That's not a one-week assignment. Pick something shorter."
            else:
                with transaction.atomic():
                    pick = WeeklyPick.objects.create(
                        couple_space=request.space, picker=request.profile, assignee=request.partner,
                        tmdb_id=tmdb_id, media_type=media_type, title=meta["title"],
                        poster_path=meta["poster_path"], backdrop_path=meta["backdrop_path"],
                        overview=meta["overview"], total_episodes=meta["total_episodes"],
                        week_start_date=start, week_end_date=end,
                        is_blind_pick=request.POST.get("is_blind_pick") == "on",
                    )
                    EpisodeProgress.objects.bulk_create([
                        EpisodeProgress(pick=pick, season_number=s, episode_number=e)
                        for s, n in meta["seasons"] for e in range(1, n + 1)
                    ])
                messages.success(request, f"Locked in. {request.partner.display_name} has until {end:%A} to finish it.")
                return redirect("dashboard")
        except ValueError:
            error = "Choose a title from the search results first."
        except tmdb.TMDBError as exc:
            error = str(exc)
        except IntegrityError:
            error = "You've already picked this week."
    return render(request, "core/pick_form.html", {"error": error, "partner": request.partner, "week_end": end})


@space_required
def pick_detail(request, pk):
    pick = get_object_or_404(WeeklyPick.objects.select_related("picker", "assignee"), pk=pk, couple_space=request.space)
    profile, partner = request.profile, request.partner
    hidden = pick.hidden_for(profile)

    seasons = []
    if not hidden:
        grouped = {}
        for ep in pick.episodes.all():
            grouped.setdefault(ep.season_number, []).append(ep)
        seasons = sorted(grouped.items())

    reviews = {r.reviewer_id: r for r in pick.reviews.all()}
    mine, theirs = reviews.get(profile.pk), reviews.get(partner.pk)
    ctx = {
        "pick": pick, "hidden": hidden, "seasons": seasons,
        "is_assignee": pick.assignee_id == profile.pk,
        "done": pick.episodes_done, "partner": partner,
        "mine": mine,
        "theirs": theirs if mine else None,
        "theirs_waiting": bool(theirs and not mine),
        "can_review": pick.status == "completed" and mine is None,
        "review_form": ReviewForm(),
    }
    return render(request, "core/pick_detail.html", ctx)


@space_required
@require_POST
def pick_reveal(request, pk):
    pick = get_object_or_404(WeeklyPick, pk=pk, couple_space=request.space, assignee=request.profile)
    pick.is_revealed = True
    pick.save(update_fields=["is_revealed"])
    return redirect("pick_detail", pk=pk)


@space_required
@require_POST
def episode_toggle(request, pk, ep_id):
    pick = get_object_or_404(WeeklyPick, pk=pk, couple_space=request.space, assignee=request.profile)
    if pick.is_blind_pick and not pick.is_revealed:
        return JsonResponse({"error": "Reveal the pick first."}, status=403)
    ep = get_object_or_404(EpisodeProgress, pk=ep_id, pick=pick)
    ep.is_completed = not ep.is_completed
    ep.completed_at = timezone.now() if ep.is_completed else None
    ep.save(update_fields=["is_completed", "completed_at"])
    done = pick.recompute_status()
    return JsonResponse({
        "completed": ep.is_completed, "done": done, "total": pick.total_episodes,
        "pct": pick.progress_pct, "status": pick.status, "status_label": pick.get_status_display(),
    })


@space_required
@require_POST
def review_create(request, pk):
    pick = get_object_or_404(WeeklyPick, pk=pk, couple_space=request.space)
    if pick.status != "completed":
        messages.error(request, "You can rate it once it's been watched.")
        return redirect("pick_detail", pk=pk)
    form = ReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Rating must be between 0 and 10.")
        return redirect("pick_detail", pk=pk)
    try:
        with transaction.atomic():
            Review.objects.create(pick=pick, reviewer=request.profile, **form.cleaned_data)
    except IntegrityError:
        messages.info(request, "You've already rated this one.")
    return redirect("pick_detail", pk=pk)
