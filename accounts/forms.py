from django import forms
from django.contrib.auth import get_user_model
from inventory.models import Department

User = get_user_model()


class UserCreateForm(forms.ModelForm):

    password = forms.CharField(
        widget=forms.PasswordInput
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "role",
            "hotel",
            "department",
            "is_department_head",
            "password"
        ]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])

        if commit:
            user.save()

        return user