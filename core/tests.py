from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import tmdb
from .models import CoupleSpace, EpisodeProgress, Profile, Review, WeeklyPick
from .utils import week_bounds


def make_profile(username, pin="7391"):
    u = User.objects.create_user(username=username)
    u.set_unusable_password()
    u.save()
    p = Profile(user=u, display_name=username.title())
    p.set_pin(pin)
    p.save()
    return p


FAKE_TV = {
    "title": "Secret Show", "poster_path": "/p.jpg", "backdrop_path": "/b.jpg", "overview": "SPOILER-FREE-BUT-REVEALING",
    "seasons": [(1, 3), (2, 2)], "total_episodes": 5, "runtime": 45,
}


class Base(TestCase):
    def setUp(self):
        self.a, self.b = make_profile("alice"), make_profile("bob")
        self.space = CoupleSpace.objects.create(partner_one=self.a, partner_two=self.b)
        self.start, self.end = week_bounds()

    def login(self, profile, pin="7391"):
        self.client.logout()
        return self.client.post(reverse("login"), {"username": profile.user.username, "pin": pin})

    def make_pick(self, picker, assignee, blind=False, n=3):
        pick = WeeklyPick.objects.create(
            couple_space=self.space, picker=picker, assignee=assignee, tmdb_id=1, title="Secret Show",
            media_type="tv", overview="SPOILER-FREE-BUT-REVEALING", total_episodes=n,
            week_start_date=self.start, week_end_date=self.end, is_blind_pick=blind,
        )
        EpisodeProgress.objects.bulk_create([EpisodeProgress(pick=pick, season_number=1, episode_number=i) for i in range(1, n + 1)])
        return pick


class AuthTests(Base):
    def test_login_and_lockout(self):
        for _ in range(5):
            self.login(self.a, pin="0000")
        r = self.login(self.a, pin="7391")  # correct PIN, but locked
        self.assertContains(r, "Too many wrong PINs")
        self.a.refresh_from_db()
        self.a.locked_until = timezone.now() - timedelta(seconds=1)
        self.a.save()
        self.assertEqual(self.login(self.a).status_code, 302)

    def test_dashboard_requires_login(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)


class PairingTests(TestCase):
    def test_join_with_code_is_single_use(self):
        a, b, c = make_profile("alice"), make_profile("bob"), make_profile("carol")
        space = CoupleSpace.objects.create(partner_one=a, invite_code="ABCD2345")
        self.client.post(reverse("login"), {"username": "bob", "pin": "7391"})
        self.client.post(reverse("pair"), {"action": "join", "code": "abcd2345"})
        space.refresh_from_db()
        self.assertEqual(space.partner_two_id, b.pk)
        self.assertIsNone(space.invite_code)
        self.client.logout()
        self.client.post(reverse("login"), {"username": "carol", "pin": "7391"})
        self.client.post(reverse("pair"), {"action": "join", "code": "ABCD2345"})
        space.refresh_from_db()
        self.assertEqual(space.partner_two_id, b.pk)  # carol did not get in


class BlindPickTests(Base):
    def test_assignee_cannot_see_blind_pick_details(self):
        pick = self.make_pick(self.a, self.b, blind=True)
        self.login(self.b)
        for url in (reverse("pick_detail", args=[pick.pk]), reverse("dashboard"), reverse("history")):
            html = self.client.get(url).content.decode()
            self.assertNotIn("Secret Show", html, url)
            self.assertNotIn("SPOILER-FREE-BUT-REVEALING", html, url)

    def test_picker_can_see_it_and_assignee_can_after_reveal(self):
        pick = self.make_pick(self.a, self.b, blind=True)
        self.login(self.a)
        self.assertContains(self.client.get(reverse("pick_detail", args=[pick.pk])), "Secret Show")
        self.login(self.b)
        self.client.post(reverse("pick_reveal", args=[pick.pk]))
        self.assertContains(self.client.get(reverse("pick_detail", args=[pick.pk])), "Secret Show")

    def test_cannot_tick_episodes_before_reveal(self):
        pick = self.make_pick(self.a, self.b, blind=True)
        ep = pick.episodes.first()
        self.login(self.b)
        r = self.client.post(reverse("episode_toggle", args=[pick.pk, ep.pk]))
        self.assertEqual(r.status_code, 403)

    def test_only_assignee_can_reveal(self):
        pick = self.make_pick(self.a, self.b, blind=True)
        self.login(self.a)
        self.assertEqual(self.client.post(reverse("pick_reveal", args=[pick.pk])).status_code, 404)


class ProgressTests(Base):
    def test_status_transitions_and_permissions(self):
        pick = self.make_pick(self.a, self.b, n=2)
        e1, e2 = pick.episodes.all()
        self.login(self.a)  # picker may not tick
        self.assertEqual(self.client.post(reverse("episode_toggle", args=[pick.pk, e1.pk])).status_code, 404)
        self.login(self.b)
        self.client.post(reverse("episode_toggle", args=[pick.pk, e1.pk]))
        pick.refresh_from_db(); self.assertEqual(pick.status, "in_progress")
        self.client.post(reverse("episode_toggle", args=[pick.pk, e2.pk]))
        pick.refresh_from_db(); self.assertEqual(pick.status, "completed")
        self.client.post(reverse("episode_toggle", args=[pick.pk, e2.pk]))  # untick
        pick.refresh_from_db(); self.assertEqual(pick.status, "in_progress")

    def test_outsider_cannot_access_pick(self):
        pick = self.make_pick(self.a, self.b)
        make_profile("mallory")
        other = CoupleSpace.objects.create(partner_one=Profile.objects.get(user__username="mallory"), partner_two=make_profile("eve"))
        self.client.post(reverse("login"), {"username": "mallory", "pin": "7391"})
        self.assertEqual(self.client.get(reverse("pick_detail", args=[pick.pk])).status_code, 404)


class ReviewTests(Base):
    def test_partner_review_hidden_until_you_rate(self):
        pick = self.make_pick(self.a, self.b, n=1)
        pick.episodes.update(is_completed=True)
        pick.recompute_status()
        Review.objects.create(pick=pick, reviewer=self.b, rating=9, comment="TWIST-ENDING-TALK")
        self.login(self.a)
        html = self.client.get(reverse("pick_detail", args=[pick.pk])).content.decode()
        self.assertNotIn("TWIST-ENDING-TALK", html)
        self.client.post(reverse("review_create", args=[pick.pk]), {"rating": "8.5", "comment": "ok"})
        html = self.client.get(reverse("pick_detail", args=[pick.pk])).content.decode()
        self.assertIn("TWIST-ENDING-TALK", html)

    def test_rating_bounds_and_uniqueness(self):
        pick = self.make_pick(self.a, self.b, n=1)
        pick.episodes.update(is_completed=True); pick.recompute_status()
        self.login(self.a)
        self.client.post(reverse("review_create", args=[pick.pk]), {"rating": "11"})
        self.assertEqual(Review.objects.count(), 0)
        self.client.post(reverse("review_create", args=[pick.pk]), {"rating": "7"})
        self.client.post(reverse("review_create", args=[pick.pk]), {"rating": "3"})
        self.assertEqual(Review.objects.filter(reviewer=self.a).count(), 1)

    def test_cannot_review_unfinished_pick(self):
        pick = self.make_pick(self.a, self.b)
        self.login(self.a)
        self.client.post(reverse("review_create", args=[pick.pk]), {"rating": "7"})
        self.assertEqual(Review.objects.count(), 0)


@override_settings(TMDB_API_KEY="x")
class PickCreationTests(Base):
    @mock.patch("core.tmdb.details", return_value=FAKE_TV)
    def test_create_pick_builds_episodes_and_blocks_second(self, _):
        self.login(self.a)
        r = self.client.post(reverse("pick_new"), {"tmdb_id": "42", "media_type": "tv", "is_blind_pick": "on"})
        self.assertEqual(r.status_code, 302)
        pick = WeeklyPick.objects.get()
        self.assertEqual((pick.assignee, pick.total_episodes, pick.episodes.count(), pick.is_blind_pick), (self.b, 5, 5, True))
        self.assertEqual(self.client.get(reverse("pick_new")).status_code, 302)  # already picked this week

    @mock.patch("core.tmdb.details", return_value={**FAKE_TV, "total_episodes": 999})
    def test_rejects_huge_series(self, _):
        self.login(self.a)
        r = self.client.post(reverse("pick_new"), {"tmdb_id": "42", "media_type": "tv"})
        self.assertContains(r, "not a one-week assignment")
        self.assertEqual(WeeklyPick.objects.count(), 0)

    def test_search_without_key_fails_cleanly(self):
        with override_settings(TMDB_API_KEY=""):
            self.login(self.a)
            r = self.client.get(reverse("api_search"), {"q": "dune"})
            self.assertEqual(r.status_code, 502)
