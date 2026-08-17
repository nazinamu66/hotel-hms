from getpass import getpass

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model


User = get_user_model()


class Command(BaseCommand):
    help = "Create the initial system administrator."

    def handle(self, *args, **options):

        if User.objects.filter(role="ADMIN").exists():
            raise CommandError(
                "An ADMIN user already exists."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Creating initial system administrator..."
            )
        )

        username = input("Username: ").strip()
        email = input("Email: ").strip()
        password = getpass("Password: ")
        password_confirm = getpass("Confirm password: ")

        if not username:
            raise CommandError("Username is required.")

        if not password:
            raise CommandError("Password is required.")

        if password != password_confirm:
            raise CommandError("Passwords do not match.")

        if User.objects.filter(username=username).exists():
            raise CommandError(
                f"Username '{username}' already exists."
            )

        user = User(
            username=username,
            email=email,
            role="ADMIN",
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )

        user.set_password(password)
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Initial administrator '{user.username}' created successfully."
            )
        )