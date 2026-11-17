from django import forms

from billing.models import Company, GroupReservation


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name",
            "registration_number",
            "contact_person",
            "phone",
            "email",
            "billing_address",
            "tax_number",
            "notes",
            "is_active",
        ]
        widgets = {
            "billing_address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class GroupReservationForm(forms.ModelForm):
    class Meta:
        model = GroupReservation
        fields = [
            "company",
            "name",
            "check_in_date",
            "check_out_date",
            "room_billing_party",
            "agreement_notes",
        ]
        widgets = {
            "check_in_date": forms.DateInput(attrs={"type": "date"}),
            "check_out_date": forms.DateInput(attrs={"type": "date"}),
            "agreement_notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.filter(
            hotel=hotel, is_active=True
        ).order_by("name") if hotel else Company.objects.none()


class GroupOccupantForm(forms.Form):
    guest_mode = forms.ChoiceField(
        choices=(("EXISTING", "Existing guest"), ("NEW", "New guest")),
        initial="EXISTING",
    )
    guest = forms.ModelChoiceField(queryset=None, required=False)
    first_name = forms.CharField(max_length=75, required=False)
    last_name = forms.CharField(max_length=75, required=False)
    phone = forms.CharField(max_length=50, required=False)
    email = forms.EmailField(required=False)
    nationality = forms.CharField(max_length=100, required=False)
    id_number = forms.CharField(max_length=100, required=False)
    room_category = forms.ModelChoiceField(queryset=None)
    room = forms.ModelChoiceField(queryset=None, required=False)
    check_in_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    check_out_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    occupant_note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, hotel=None, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        from billing.models import Guest
        from rooms.models import Room, RoomCategory
        self.fields["guest"].queryset = Guest.objects.filter(hotel=hotel).order_by("first_name", "last_name") if hotel else Guest.objects.none()
        self.fields["room_category"].queryset = RoomCategory.objects.filter(
            is_active=True, rooms__hotel=hotel
        ).distinct().order_by("name") if hotel else RoomCategory.objects.none()
        self.fields["room"].queryset = Room.objects.filter(
            hotel=hotel, status__in=["AVAILABLE", "VACANT_DIRTY"]
        ).select_related("category").order_by("room_number") if hotel else Room.objects.none()
        if group:
            self.fields["check_in_date"].initial = group.check_in_date
            self.fields["check_out_date"].initial = group.check_out_date

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("guest_mode")
        if mode == "EXISTING" and not cleaned.get("guest"):
            self.add_error("guest", "Select an existing guest.")
        if mode == "NEW":
            if not cleaned.get("first_name") or not cleaned.get("last_name"):
                self.add_error("first_name", "First and last name are required for a new guest.")
        return cleaned
