from django.db import models
from django.conf import settings
from rooms.models import Room


class CleaningLog(models.Model):

    room = models.ForeignKey(
        Room,
        on_delete=models.PROTECT,
        related_name="cleaning_logs"
    )

    cleaned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )

    cleaned_at = models.DateTimeField(auto_now_add=True)

    previous_status = models.CharField(max_length=50)

    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Room {self.room.room_number} cleaned at {self.cleaned_at}"