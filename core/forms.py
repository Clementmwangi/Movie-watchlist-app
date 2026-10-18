from decimal import Decimal

from django import forms
from django.contrib.auth.models import User

AVATAR_COLORS = ["indigo", "rose", "emerald", "amber", "sky", "violet"]
WEAK_PINS = {"1234", "4321", "0123", "2580", "1212", "2024", "2025", "2026"}


class SignupForm(forms.Form):
    username = forms.RegexField(r"^[A-Za-z0-9_]{3,30}$", error_messages={"invalid": "3–30 letters, numbers or underscores."})
    display_name = forms.CharField(max_length=50)
    avatar_color = forms.ChoiceField(choices=[(c, c) for c in AVATAR_COLORS])
    pin = forms.RegexField(r"^\d{4}$", error_messages={"invalid": "PIN must be exactly 4 digits."})
    pin_confirm = forms.CharField()

    def clean_username(self):
        u = self.cleaned_data["username"].lower()
        if User.objects.filter(username__iexact=u).exists():
            raise forms.ValidationError("That username is taken.")
        return u

    def clean(self):
        data = super().clean()
        pin = data.get("pin")
        if pin:
            if data.get("pin_confirm") != pin:
                self.add_error("pin_confirm", "PINs don't match.")
            if len(set(pin)) == 1 or pin in WEAK_PINS:
                self.add_error("pin", "Too guessable. Pick something that isn't a repeat or a sequence.")
        return data


class LoginForm(forms.Form):
    username = forms.CharField(max_length=30)
    pin = forms.RegexField(r"^\d{4}$")


class ReviewForm(forms.Form):
    rating = forms.DecimalField(min_value=Decimal("0"), max_value=Decimal("10"), max_digits=3, decimal_places=1)
    comment = forms.CharField(required=False, widget=forms.Textarea, max_length=2000)
    favorite_quote_or_scene = forms.CharField(required=False, max_length=255)
