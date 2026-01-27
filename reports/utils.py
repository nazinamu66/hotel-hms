from django.utils.timezone import now
from datetime import time

def today_range():
    today = now().date()
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = now().replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end
