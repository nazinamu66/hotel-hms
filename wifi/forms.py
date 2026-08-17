from django import forms

from .models import WiFiProfile

from billing.models import Guest
from django import forms

from .models import WiFiProfile, WiFiDevice, WiFiVoucher



class WiFiProfileForm(forms.ModelForm):

    class Meta:
        model = WiFiProfile

        fields = [
            "name",
            "description",
            "download_speed_mbps",
            "upload_speed_mbps",
            "max_devices",
            "session_timeout_minutes",
            "is_active",
        ]

        widgets = {
            "description": forms.TextInput(
                attrs={
                    "class": "form-control",
                }
            ),
            "download_speed_mbps": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "upload_speed_mbps": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "max_devices": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "session_timeout_minutes": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
        }


class WiFiProfileForm(forms.ModelForm):

    class Meta:
        model = WiFiProfile

        fields = [
            "name",
            "description",
            "download_speed_mbps",
            "upload_speed_mbps",
            "max_devices",
            "session_timeout_minutes",
            "is_active",
        ]

        widgets = {
            "description": forms.TextInput(
                attrs={
                    "class": "form-control",
                }
            ),
            "download_speed_mbps": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "upload_speed_mbps": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "max_devices": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
            "session_timeout_minutes": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1,
                }
            ),
        }


class WiFiGuestAccountForm(forms.Form):

    guest = forms.ModelChoiceField(
        queryset=Guest.objects.none(),
        empty_label="Select guest",
        widget=forms.Select(
            attrs={
                "class": "form-control",
            }
        ),
    )

    profile = forms.ModelChoiceField(
        queryset=WiFiProfile.objects.none(),
        empty_label="Select Wi-Fi profile",
        widget=forms.Select(
            attrs={
                "class": "form-control",
            }
        ),
    )

    valid_from = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control",
                "type": "datetime-local",
            }
        ),
    )

    valid_until = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control",
                "type": "datetime-local",
            }
        ),
    )

    def __init__(self, *args, hotel=None, **kwargs):

        super().__init__(*args, **kwargs)

        if hotel is not None:

            self.fields["guest"].queryset = (
                Guest.objects
                .filter(hotel=hotel)
                .order_by("first_name", "last_name")
            )

            self.fields["profile"].queryset = (
                WiFiProfile.objects
                .filter(
                    hotel=hotel,
                    is_active=True,
                )
                .order_by("name")
            )

    def clean(self):

        cleaned_data = super().clean()

        guest = cleaned_data.get("guest")
        profile = cleaned_data.get("profile")
        valid_from = cleaned_data.get("valid_from")
        valid_until = cleaned_data.get("valid_until")

        if valid_from and valid_until:

            if valid_until <= valid_from:
                self.add_error(
                    "valid_until",
                    "Valid until must be later than valid from.",
                )

        if guest and profile:

            if guest.hotel_id != profile.hotel_id:
                raise forms.ValidationError(
                    "The selected guest and Wi-Fi profile "
                    "must belong to the same hotel."
                )

        return cleaned_data
from .models import WiFiDevice


class WiFiDeviceForm(forms.ModelForm):

    class Meta:
        model = WiFiDevice

        fields = [
            "name",
            "mac_address",
            "device_type",
            "is_exempt",
            "is_active",
            "description",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                }
            ),
            "mac_address": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "AA:BB:CC:11:22:33",
                }
            ),
            "device_type": forms.Select(
                attrs={
                    "class": "form-control",
                }
            ),
            "is_exempt": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "description": forms.TextInput(
                attrs={
                    "class": "form-control",
                }
            ),
        }

    def clean_mac_address(self):
        mac = self.cleaned_data["mac_address"].strip().upper()

        # Normalize common separators.
        mac = mac.replace("-", ":")

        return mac


class WiFiVoucherForm(forms.Form):

    profile = forms.ModelChoiceField(
        queryset=WiFiProfile.objects.none(),
        empty_label="Select Wi-Fi profile",
        widget=forms.Select(
            attrs={
                "class": "form-control",
            }
        ),
    )

    valid_from = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control",
                "type": "datetime-local",
            }
        )
    )

    valid_until = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={
                "class": "form-control",
                "type": "datetime-local",
            }
        )
    )

    max_devices = forms.IntegerField(
        min_value=1,
        initial=1,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": 1,
            }
        ),
    )

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
            }
        ),
    )

    def __init__(self, *args, hotel=None, **kwargs):

        super().__init__(*args, **kwargs)

        # IMPORTANT:
        # Keep the hotel available to clean()
        self.hotel = hotel

        if hotel is not None:

            self.fields["profile"].queryset = (
                WiFiProfile.objects
                .filter(
                    hotel=hotel,
                    is_active=True,
                )
                .order_by("name")
            )

    def clean(self):

        cleaned_data = super().clean()

        profile = cleaned_data.get("profile")
        valid_from = cleaned_data.get("valid_from")
        valid_until = cleaned_data.get("valid_until")

        if valid_from and valid_until:

            if valid_until <= valid_from:

                self.add_error(
                    "valid_until",
                    "Valid until must be later than valid from.",
                )

        if (
            profile
            and self.hotel is not None
            and profile.hotel_id != self.hotel.id
        ):

            raise forms.ValidationError(
                "The selected Wi-Fi profile does not belong "
                "to this hotel."
            )

        return cleaned_data