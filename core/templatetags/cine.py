from django import template

from core.tmdb import IMG_BASE

register = template.Library()

AVATARS = {
    "indigo": "bg-indigo-500", "rose": "bg-rose-500", "emerald": "bg-emerald-500",
    "amber": "bg-amber-500", "sky": "bg-sky-500", "violet": "bg-violet-500",
}


@register.filter
def tmdb(path, size="w500"):
    return f"{IMG_BASE}/{size}{path}" if path else ""


@register.filter
def avatar_bg(color):
    return AVATARS.get(color, "bg-indigo-500")


@register.filter
def initial(name):
    return (name or "?")[:1].upper()


@register.filter
def hidden_for(pick, profile):
    return pick.hidden_for(profile)
