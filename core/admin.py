from django.contrib import admin

from .models import CoupleSpace, EpisodeProgress, Profile, Review, WeeklyPick

admin.site.register(Profile)
admin.site.register(CoupleSpace)
admin.site.register(WeeklyPick)
admin.site.register(EpisodeProgress)
admin.site.register(Review)
