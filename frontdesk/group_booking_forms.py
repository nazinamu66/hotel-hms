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


class GroupRoomRatesForm(forms.Form):
    """One optional negotiated nightly rate per room category for this group."""

    def __init__(self, *args, categories=None, existing_rates=None, **kwargs):
        super().__init__(*args, **kwargs)
        existing_rates = existing_rates or {}
        self.categories = list(categories or [])
        for category in self.categories:
            current = existing_rates.get(category.pk)
            self.fields[f"rate_{category.pk}"] = forms.DecimalField(
                label=category.name,
                required=False,
                min_value=0.01,
                max_digits=12,
                decimal_places=2,
                initial=current.nightly_rate if current else None,
                widget=forms.NumberInput(attrs={
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "Use standard rate",
                }),
            )


from billing.models import EventBooking, EventParticipant, EventOrderLine, GroupReservationRoom


class EventBookingForm(forms.ModelForm):
    class Meta:
        model = EventBooking
        fields = ["company", "group_reservation", "name", "event_type", "event_date", "service_time", "location", "expected_participants", "agreement_notes"]
        widgets = {
            "event_date": forms.DateInput(attrs={"type": "date"}),
            "service_time": forms.TimeInput(attrs={"type": "time"}),
            "agreement_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, hotel=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.filter(hotel=hotel, is_active=True).order_by("name") if hotel else Company.objects.none()
        self.fields["group_reservation"].queryset = GroupReservation.objects.filter(hotel=hotel).exclude(status="CANCELLED").select_related("company") if hotel else GroupReservation.objects.none()
        self.fields["group_reservation"].required = False

    def clean(self):
        cleaned = super().clean()
        group = cleaned.get("group_reservation")
        company = cleaned.get("company")
        if group and company and group.company_id != company.pk:
            self.add_error("group_reservation", "Select a group reservation belonging to this company.")
        return cleaned


class EventOrderLineForm(forms.ModelForm):
    class Meta:
        model = EventOrderLine
        fields = ["line_type", "description", "quantity", "unit_price", "notes"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


class EventParticipantForm(forms.ModelForm):
    class Meta:
        model = EventParticipant
        fields = ["participant_type", "reservation", "external_name", "external_organization", "note"]
        widgets = {"note": forms.TextInput(attrs={"placeholder": "Optional attendee note"})}

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        reservations = Reservation.objects.filter(
            hotel=event.hotel,
            status="CHECKED_IN",
            room__isnull=False,
        ).select_related("guest", "room").order_by("guest__last_name", "guest__first_name") if event else Reservation.objects.none()
        self.fields["reservation"].queryset = reservations
        self.fields["reservation"].required = False
        self.fields["reservation"].label = "Checked-in guest / room"

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("participant_type")
        reservation = cleaned.get("reservation")
        if kind == "IN_HOUSE" and not reservation:
            self.add_error("reservation", "Select a checked-in hotel guest and room.")
        if kind == "EXTERNAL" and not (cleaned.get("external_name") or "").strip():
            self.add_error("external_name", "Enter the participant's name.")
        return cleaned
