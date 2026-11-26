from django.contrib import admin

from .models import GymMember, GymMembership, GymMembershipPlan, GymVisit


@admin.register(GymMembershipPlan)
class GymMembershipPlanAdmin(admin.ModelAdmin):
    list_display = ("hotel", "package", "price", "is_active", "updated_at")
    list_filter = ("hotel", "package", "is_active")


@admin.register(GymMember)
class GymMemberAdmin(admin.ModelAdmin):
    list_display = ("member_number", "full_name", "hotel", "phone", "is_active", "created_at")
    list_filter = ("hotel", "is_active")
    search_fields = ("member_number", "first_name", "last_name", "phone", "email")
    readonly_fields = ("member_number", "created_at", "updated_at")


class GymVisitInline(admin.TabularInline):
    model = GymVisit
    extra = 0
    readonly_fields = ("entered_at", "exited_at", "checked_in_by", "checked_out_by")


@admin.register(GymMembership)
class GymMembershipAdmin(admin.ModelAdmin):
    list_display = ("member", "plan_name_snapshot", "start_date", "end_date", "status", "amount_snapshot")
    list_filter = ("hotel", "status", "plan__package")
    search_fields = ("member__member_number", "member__first_name", "member__last_name")
    readonly_fields = ("pass_token", "created_at", "updated_at")
    inlines = (GymVisitInline,)


@admin.register(GymVisit)
class GymVisitAdmin(admin.ModelAdmin):
    list_display = ("membership", "hotel", "entered_at", "exited_at", "checked_in_by")
    list_filter = ("hotel", "entered_at")
    search_fields = ("membership__member__member_number", "membership__member__first_name", "membership__member__last_name")
