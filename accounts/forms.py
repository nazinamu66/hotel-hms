from django import forms
from django.contrib.auth import get_user_model
from inventory.models import HotelFeature

from inventory.models import (
    Organization,
    Hotel,
    Department,
)

User = get_user_model()


class InitialOrganizationForm(forms.Form):

    organization_name = forms.CharField(
        max_length=150,
        label="Organization Name",
    )


class InitialHotelProfileForm(forms.Form):

    hotel_name = forms.CharField(
        max_length=150,
        label="Hotel Name",
    )

    address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Address",
    )

    city = forms.CharField(
        max_length=100,
        label="City",
    )

    state = forms.CharField(
        max_length=100,
        label="State",
    )

    country = forms.CharField(
        max_length=100,
        label="Country",
        initial="Nigeria",
    )

    phone = forms.CharField(
        max_length=50,
        label="Phone",
    )

    email = forms.EmailField(
        required=False,
        label="Email",
    )

    website = forms.URLField(
        required=False,
        label="Website",
    )

    logo = forms.ImageField(
        required=False,
        label="Logo",
    )

    tax_number = forms.CharField(
        max_length=50,
        required=False,
        label="Tax / Registration Number",
    )

    currency = forms.CharField(
        max_length=10,
        initial="₦",
        label="Currency",
    )


class InitialFeaturesForm(forms.Form):

    features = forms.MultipleChoiceField(
        choices=HotelFeature.FEATURE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Hotel Features",
    )


class InitialDepartmentsForm(forms.Form):
    """
    Departments are created automatically.
    This form exists only so Screen 4 has
    a consistent wizard step.
    """

    confirm = forms.BooleanField(
        required=True,
        label="I understand that these standard departments will be created automatically.",
    )


class InitialAccountingForm(forms.Form):
    """
    Accounting structure is created automatically.
    """

    confirm = forms.BooleanField(
        required=True,
        label="I understand that the default accounting structure will be created automatically.",
    )


class InitialSetupReviewForm(forms.Form):
    confirm = forms.BooleanField(
        required=True,
        label="I confirm that the setup information is correct.",
    )


class UserCreateForm(forms.ModelForm):

    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
    )

    class Meta:
        model = User

        fields = [
            "username",
            "email",
            "role",
            "organization",
            "hotel",
            "assigned_hotels",
            "department",
            "is_department_head",
            "password",
        ]

    def __init__(
        self,
        *args,
        actor=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.actor = actor

        # -----------------------------------------------------
        # DEFAULT QUERYSETS
        # -----------------------------------------------------

        self.fields["organization"].queryset = (
            Organization.objects.all()
        )

        self.fields["hotel"].queryset = (
            Hotel.objects.filter(
                is_active=True,
            ).order_by("name")
        )

        self.fields["assigned_hotels"].queryset = (
            Hotel.objects.filter(
                is_active=True,
            ).order_by("name")
        )

        self.fields["department"].queryset = (
            Department.objects.filter(
                is_active=True,
            )
            .select_related("hotel")
            .order_by(
                "hotel__name",
                "name",
            )
        )

        # -----------------------------------------------------
        # ACTOR SCOPE
        # -----------------------------------------------------

        if actor:

            if actor.role == "DIRECTOR":

                self.fields["organization"].queryset = (
                    Organization.objects.filter(
                        pk=actor.organization_id,
                    )
                )

                self.fields["hotel"].queryset = (
                    Hotel.objects.filter(
                        organization_id=actor.organization_id,
                        is_active=True,
                    ).order_by("name")
                )

                self.fields["assigned_hotels"].queryset = (
                    Hotel.objects.filter(
                        organization_id=actor.organization_id,
                        is_active=True,
                    ).order_by("name")
                )

                self.fields["department"].queryset = (
                    Department.objects.filter(
                        hotel__organization_id=actor.organization_id,
                        is_active=True,
                    )
                    .select_related("hotel")
                    .order_by(
                        "hotel__name",
                        "name",
                    )
                )

                # Director cannot create another Admin.
                self.fields["role"].choices = [
                    choice
                    for choice in self.fields["role"].choices
                    if choice[0] != "ADMIN"
                ]

        # -----------------------------------------------------
        # ORGANIZATION INITIAL VALUE
        # -----------------------------------------------------

        if (
            actor
            and actor.role == "DIRECTOR"
            and actor.organization_id
            and not self.instance.organization_id
        ):
            self.initial["organization"] = actor.organization_id

        # -----------------------------------------------------
        # PASSWORD LABEL
        # -----------------------------------------------------

        if self.instance.pk:
            self.fields["password"].label = (
                "Password (leave blank to keep current password)"
            )
        else:
            self.fields["password"].required = True
    # =========================================================
    # VALIDATION
    # =========================================================

    def clean(self):

        cleaned_data = super().clean()

        role = cleaned_data.get("role")
        organization = cleaned_data.get("organization")
        hotel = cleaned_data.get("hotel")
        assigned_hotels = cleaned_data.get("assigned_hotels")
        department = cleaned_data.get("department")
        is_department_head = cleaned_data.get(
            "is_department_head"
        )

        # -----------------------------------------------------
        # ACTOR
        # -----------------------------------------------------

        if not self.actor:
            raise forms.ValidationError(
                "Unable to determine the user creating this account."
            )
        # -----------------------------------------------------
        # DIRECTOR ROLE RESTRICTION
        # -----------------------------------------------------

        if (
            self.actor.role == "DIRECTOR"
            and role == "ADMIN"
        ):
            self.add_error(
                "role",
                "Directors cannot create or assign the Admin role.",
            )
        # -----------------------------------------------------
        # ORGANIZATION-LEVEL ROLES
        # -----------------------------------------------------

        if role in {
            "DIRECTOR",
            "GENERAL_MANAGER",
            "CHIEF_ACCOUNTANT",
        }:

            if not organization:
                self.add_error(
                    "organization",
                    "This role requires an organization.",
                )

            if organization and self.actor.role == "DIRECTOR":

                if organization.pk != self.actor.organization_id:
                    self.add_error(
                        "organization",
                        "You can only create users "
                        "within your organization.",
                    )

        # -----------------------------------------------------
        # GENERAL MANAGER
        # -----------------------------------------------------

        if role == "GENERAL_MANAGER":

            if not assigned_hotels:
                self.add_error(
                    "assigned_hotels",
                    "Select at least one hotel.",
                )

            if organization and assigned_hotels:

                invalid_hotels = [
                    h
                    for h in assigned_hotels
                    if h.organization_id != organization.pk
                ]

                if invalid_hotels:
                    self.add_error(
                        "assigned_hotels",
                        "All assigned hotels must belong "
                        "to the selected organization.",
                    )

        # -----------------------------------------------------
        # HOTEL-LEVEL ROLES
        # -----------------------------------------------------

        if role in {
            "MANAGER",
            "ACCOUNTANT",
        }:

            if not hotel:
                self.add_error(
                    "hotel",
                    "This role requires a hotel.",
                )

            elif self.actor.role == "DIRECTOR":

                if hotel.organization_id != self.actor.organization_id:
                    self.add_error(
                        "hotel",
                        "You can only assign users "
                        "to hotels in your organization.",
                    )

            if hotel:
                cleaned_data["organization"] = (
                    hotel.organization
                )

        # -----------------------------------------------------
        # OPERATIONAL ROLES
        # -----------------------------------------------------

        operational_roles = {
            "FRONTDESK",
            "RESTAURANT",
            "STORE",
            "KITCHEN",
            "HOUSEKEEPING",
            "LAUNDRY",
            "GYM",
            "BOUTIQUE",
        }

        if role in operational_roles:

            if not department:
                self.add_error(
                    "department",
                    "This role requires a department.",
                )

            else:

                department_hotel = department.hotel

                # Department determines hotel.
                cleaned_data["hotel"] = department_hotel
                cleaned_data["organization"] = (
                    department_hotel.organization
                )

                # Director scope
                if self.actor.role == "DIRECTOR":

                    if (
                        department_hotel.organization_id
                        != self.actor.organization_id
                    ):
                        self.add_error(
                            "department",
                            "You can only assign users "
                            "to departments in your organization.",
                        )

        # -----------------------------------------------------
        # DEPARTMENT HEAD
        # -----------------------------------------------------

        if is_department_head and role not in operational_roles:

            self.add_error(
                "is_department_head",
                "Only operational staff can be "
                "marked as Head of Department.",
            )

        if is_department_head and not department:

            self.add_error(
                "is_department_head",
                "A department head must belong "
                "to a department.",
            )

        # -----------------------------------------------------
        # CLEANUP ROLE-SPECIFIC FIELDS
        # -----------------------------------------------------

        if role in {
            "ADMIN",
            "DIRECTOR",
            "GENERAL_MANAGER",
            "CHIEF_ACCOUNTANT",
        }:

            cleaned_data["hotel"] = None
            cleaned_data["department"] = None

        if role != "GENERAL_MANAGER":
            cleaned_data["assigned_hotels"] = []

        if role not in {
            "MANAGER",
            "ACCOUNTANT",
        } and role not in operational_roles:

            cleaned_data["hotel"] = None

        return cleaned_data

    # =========================================================
    # SAVE
    # =========================================================

    def save(self, commit=True):

        user = super().save(commit=False)

        password = self.cleaned_data.get("password")

        if password:
            user.set_password(password)

        role = self.cleaned_data.get("role")

        # -----------------------------------------------------
        # ORGANIZATION-LEVEL
        # -----------------------------------------------------

        if role in {
            "DIRECTOR",
            "GENERAL_MANAGER",
            "CHIEF_ACCOUNTANT",
        }:

            user.organization = (
                self.cleaned_data.get("organization")
            )

            user.hotel = None
            user.department = None

        # -----------------------------------------------------
        # HOTEL-LEVEL
        # -----------------------------------------------------

        elif role in {
            "MANAGER",
            "ACCOUNTANT",
        }:

            hotel = self.cleaned_data.get("hotel")

            user.hotel = hotel
            user.organization = (
                hotel.organization
                if hotel
                else None
            )

            user.department = None

        # -----------------------------------------------------
        # OPERATIONAL
        # -----------------------------------------------------

        elif role in {
            "FRONTDESK",
            "RESTAURANT",
            "STORE",
            "KITCHEN",
            "HOUSEKEEPING",
            "LAUNDRY",
            "GYM",
            "BOUTIQUE",
        }:

            department = (
                self.cleaned_data.get("department")
            )

            user.department = department

            if department:
                user.hotel = department.hotel
                user.organization = (
                    department.hotel.organization
                )

        # -----------------------------------------------------
        # ADMIN
        # -----------------------------------------------------

        elif role == "ADMIN":

            user.organization = None
            user.hotel = None
            user.department = None

        # -----------------------------------------------------
        # FINAL MODEL VALIDATION
        # -----------------------------------------------------

        user.full_clean()

        if commit:
            user.save()

            if role == "GENERAL_MANAGER":
                user.assigned_hotels.set(
                    self.cleaned_data.get("assigned_hotels")
                )
            else:
                user.assigned_hotels.clear()

        return user