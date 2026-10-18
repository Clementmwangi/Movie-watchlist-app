import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

MAX_PIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=50)
    avatar_color = models.CharField(max_length=20, default="indigo")
    pin_hash = models.CharField(max_length=128)  # hashed 4-digit PIN
    # Added: a 4-digit PIN is only 10,000 guesses. Without lockout it's decoration.
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.display_name

    def set_pin(self, raw_pin):
        self.pin_hash = make_password(raw_pin)

    def check_pin(self, raw_pin):
        return check_password(raw_pin, self.pin_hash)

    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def register_failure(self):
        self.failed_attempts += 1
        if self.failed_attempts >= MAX_PIN_ATTEMPTS:
            self.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_MINUTES)
            self.failed_attempts = 0
        self.save(update_fields=["failed_attempts", "locked_until"])

    def reset_failures(self):
        if self.failed_attempts or self.locked_until:
            self.failed_attempts = 0
            self.locked_until = None
            self.save(update_fields=["failed_attempts", "locked_until"])

    @property
    def space(self):
        return (
            CoupleSpace.objects.filter(Q(partner_one=self) | Q(partner_two=self))
            .select_related("partner_one", "partner_two")
            .first()
        )


class CoupleSpace(models.Model):
    partner_one = models.ForeignKey(Profile, related_name="space_one", on_delete=models.CASCADE)
    # Deviation from the spec: nullable, because the space has to exist
    # (with an invite code) before partner two has signed up.
    partner_two = models.ForeignKey(Profile, related_name="space_two", on_delete=models.CASCADE, null=True, blank=True)
    invite_code = models.CharField(max_length=8, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.partner_one} & {self.partner_two or '…'}"

    @staticmethod
    def new_invite_code():
        return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))

    def partner_of(self, profile):
        return self.partner_two if profile.pk == self.partner_one_id else self.partner_one


class WeeklyPick(models.Model):
    MEDIA_TYPES = [("movie", "Movie"), ("tv", "TV Series")]
    STATUS_CHOICES = [("pending", "Pending"), ("in_progress", "In Progress"), ("completed", "Completed")]

    couple_space = models.ForeignKey(CoupleSpace, on_delete=models.CASCADE)
    picker = models.ForeignKey(Profile, related_name="picks_made", on_delete=models.CASCADE)
    assignee = models.ForeignKey(Profile, related_name="picks_assigned", on_delete=models.CASCADE)

    tmdb_id = models.IntegerField()
    title = models.CharField(max_length=255)
    media_type = models.CharField(max_length=10, choices=MEDIA_TYPES)
    poster_path = models.CharField(max_length=255, blank=True, null=True)
    backdrop_path = models.CharField(max_length=255, blank=True, null=True)
    overview = models.TextField(blank=True)
    total_episodes = models.IntegerField(default=1)

    week_start_date = models.DateField()
    week_end_date = models.DateField()

    is_blind_pick = models.BooleanField(default=False)
    is_revealed = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start_date", "-created_at"]
        constraints = [
            # One pick per person per week. The spec didn't enforce this; double-submits would.
            models.UniqueConstraint(fields=["couple_space", "picker", "week_start_date"], name="one_pick_per_picker_per_week"),
        ]

    def __str__(self):
        return f"{self.title} ({self.picker} → {self.assignee})"

    def hidden_for(self, profile):
        """Blind pick: the assignee must not see title/art/synopsis until they reveal it.
        Enforced in views/templates, never by CSS alone."""
        return self.is_blind_pick and not self.is_revealed and profile.pk == self.assignee_id

    @property
    def episodes_done(self):
        return self.episodes.filter(is_completed=True).count()

    @property
    def progress_pct(self):
        return round(100 * self.episodes_done / self.total_episodes) if self.total_episodes else 0

    @property
    def days_left(self):
        return (self.week_end_date - timezone.localdate()).days + 1

    @property
    def is_overdue(self):
        return self.status != "completed" and timezone.localdate() > self.week_end_date

    @property
    def required_pace(self):
        """Episodes per remaining day to finish on time."""
        remaining = self.total_episodes - self.episodes_done
        return round(remaining / max(self.days_left, 1), 1)

    def recompute_status(self):
        done = self.episodes_done
        if done >= self.total_episodes:
            self.status, self.is_revealed = "completed", True
        elif done > 0:
            self.status = "in_progress"
        else:
            self.status = "pending"
        self.save(update_fields=["status", "is_revealed"])
        return done


class EpisodeProgress(models.Model):
    pick = models.ForeignKey(WeeklyPick, related_name="episodes", on_delete=models.CASCADE)
    season_number = models.IntegerField(default=1)
    episode_number = models.IntegerField()
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["season_number", "episode_number"]
        constraints = [
            models.UniqueConstraint(fields=["pick", "season_number", "episode_number"], name="unique_episode_per_pick"),
        ]


class Review(models.Model):
    pick = models.ForeignKey(WeeklyPick, related_name="reviews", on_delete=models.CASCADE)
    reviewer = models.ForeignKey(Profile, on_delete=models.CASCADE)
    rating = models.DecimalField(
        max_digits=3, decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    comment = models.TextField(blank=True)
    favorite_quote_or_scene = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pick", "reviewer"], name="one_review_per_reviewer"),
        ]
