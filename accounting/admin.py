from django.contrib import admin
from .models import Account, JournalEntry, JournalLine


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 1


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):

    inlines = [JournalLineInline]

    list_display = ("id", "date", "description")


admin.site.register(Account)