"""Private filesystem storage for employee documents.

The root is deliberately outside MEDIA_ROOT, so Django's public media route
cannot serve these files. Production deployments should point this setting at
a persistent private volume or replace this backend with private object storage.
"""
from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateEmployeeStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "location",
            getattr(
                settings,
                "PERSONNEL_PRIVATE_FILE_ROOT",
                settings.BASE_DIR / "private_employee_files",
            ),
        )
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)


private_employee_storage = PrivateEmployeeStorage()
