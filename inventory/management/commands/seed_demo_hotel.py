from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from billing.models import Guest, Reservation
from inventory.models import Department, Hotel, HotelFeature
from rooms.models import (
    Building,
    Floor,
    Room,
    RoomCategory,
    RoomRate,
)


class Command(BaseCommand):
    help = "Create a demo hotel environment for development and testing."

    @transaction.atomic
    def handle(self, *args, **options):

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Creating demo hotel environment..."
            )
        )

        # --------------------------------------------------
        # HOTEL
        # --------------------------------------------------

        hotel, _ = Hotel.objects.get_or_create(
            slug="demo-hotel",
            defaults={
                "name": "Demo Hotel",
                "location": "Kano, Nigeria",
                "is_active": True,
            },
        )

        # --------------------------------------------------
        # DEPARTMENTS
        # --------------------------------------------------

        departments = {}

        department_data = [
            (
                "FRONTDESK",
                "Frontdesk",
                "FRONTDESK",
            ),
            (
                "HOUSEKEEP",
                "Housekeeping",
                "HOUSEKEEPING",
            ),
            (
                "ACCOUNTING",
                "Accounting",
                "ACCOUNTING",
            ),
            (
                "MAINT",
                "Maintenance",
                "MAINTENANCE",
            ),
        ]

        for code, name, department_type in department_data:

            department, _ = Department.objects.get_or_create(
                hotel=hotel,
                code=code,
                defaults={
                    "name": name,
                    "department_type": department_type,
                    "is_active": True,
                },
            )

            departments[department_type] = department

        # --------------------------------------------------
        # USERS
        # --------------------------------------------------

        admin, created = User.objects.get_or_create(
            username="demo_admin",
            defaults={
                "role": "ADMIN",
                "is_active": True,
            },
        )

        if created:
            admin.set_password("DemoAdmin123!")
            admin.save()

        manager, created = User.objects.get_or_create(
            username="demo_manager",
            defaults={
                "role": "MANAGER",
                "is_active": True,
                "hotel": hotel,
            },
        )

        if created:
            manager.set_password("DemoManager123!")
            manager.save()

        receptionist, created = User.objects.get_or_create(
            username="demo_reception",
            defaults={
                "role": "FRONTDESK",
                "is_active": True,
                "department": departments["FRONTDESK"],
            },
        )

        if created:
            receptionist.set_password("DemoReception123!")
            receptionist.save()

        housekeeper, created = User.objects.get_or_create(
            username="demo_housekeeper",
            defaults={
                "role": "HOUSEKEEPING",
                "is_active": True,
                "department": departments["HOUSEKEEPING"],
            },
        )

        if created:
            housekeeper.set_password("DemoHousekeeper123!")
            housekeeper.save()

        # --------------------------------------------------
        # HOTEL FEATURES
        # --------------------------------------------------

        for feature in [
            "HOUSEKEEPING",
            "WIFI",
            "RESTAURANT",
            "LAUNDRY",
        ]:

            HotelFeature.objects.get_or_create(
                hotel=hotel,
                feature=feature,
                defaults={
                    "is_active": True,
                },
            )

        # --------------------------------------------------
        # BUILDING
        # --------------------------------------------------

        building, _ = Building.objects.get_or_create(
            hotel=hotel,
            name="Main Building",
            defaults={
                "is_active": True,
            },
        )

        # --------------------------------------------------
        # FLOORS
        # --------------------------------------------------

        ground_floor, _ = Floor.objects.get_or_create(
            building=building,
            number=0,
            defaults={
                "name": "Ground Floor",
                "is_active": True,
            },
        )

        first_floor, _ = Floor.objects.get_or_create(
            building=building,
            number=1,
            defaults={
                "name": "First Floor",
                "is_active": True,
            },
        )

        # --------------------------------------------------
        # ROOM CATEGORIES
        # --------------------------------------------------

        standard, _ = RoomCategory.objects.get_or_create(
            name="Standard",
            defaults={
                "description": "Standard guest room",
                "is_active": True,
            },
        )

        deluxe, _ = RoomCategory.objects.get_or_create(
            name="Deluxe",
            defaults={
                "description": "Deluxe guest room",
                "is_active": True,
            },
        )

        # --------------------------------------------------
        # ROOM RATES
        # --------------------------------------------------

        RoomRate.objects.update_or_create(
            category=standard,
            defaults={
                "price_per_night": "25000.00",
                "currency": "NGN",
            },
        )

        RoomRate.objects.update_or_create(
            category=deluxe,
            defaults={
                "price_per_night": "40000.00",
                "currency": "NGN",
            },
        )

        # --------------------------------------------------
        # ROOMS
        # --------------------------------------------------

        room_data = [
            ("101", standard, ground_floor),
            ("102", standard, ground_floor),
            ("103", standard, ground_floor),
            ("104", deluxe, ground_floor),
            ("201", deluxe, first_floor),
            ("202", deluxe, first_floor),
        ]

        rooms = {}

        for room_number, category, floor in room_data:

            room, _ = Room.objects.update_or_create(
                hotel=hotel,
                room_number=room_number,
                defaults={
                    "category": category,
                    "building": building,
                    "floor": floor,
                    "status": "AVAILABLE",
                },
            )

            rooms[room_number] = room

        # --------------------------------------------------
        # GUESTS
        # --------------------------------------------------

        john, _ = Guest.objects.get_or_create(
            hotel=hotel,
            first_name="John",
            last_name="Demo Guest",
            defaults={
                "phone": "08000000001",
                "email": "john.demo@example.com",
                "nationality": "Nigerian",
            },
        )

        amina, _ = Guest.objects.get_or_create(
            hotel=hotel,
            first_name="Amina",
            last_name="Demo Guest",
            defaults={
                "phone": "08000000002",
                "email": "amina.demo@example.com",
                "nationality": "Nigerian",
            },
        )

        # --------------------------------------------------
        # RESERVATION
        # --------------------------------------------------

        check_in = timezone.now().date() + timedelta(days=1)
        check_out = check_in + timedelta(days=2)

        Reservation.objects.get_or_create(
            guest=john,
            hotel=hotel,
            room_category=standard,
            check_in_date=check_in,
            check_out_date=check_out,
            defaults={
                "source": "DEMO",
                "note": "Demo reservation created by seed command.",
                "status": "RESERVED",
                "created_by": receptionist,
            },
        )

        # --------------------------------------------------
        # OUTPUT
        # --------------------------------------------------

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Demo hotel created successfully."
            )
        )

        self.stdout.write(
            f"Hotel: {hotel.name}"
        )

        self.stdout.write(
            f"Rooms: {Room.objects.filter(hotel=hotel).count()}"
        )

        self.stdout.write(
            f"Guests: {Guest.objects.filter(hotel=hotel).count()}"
        )

        self.stdout.write(
            f"Reservations: "
            f"{Reservation.objects.filter(hotel=hotel).count()}"
        )

        self.stdout.write("")
        self.stdout.write("Demo accounts:")
        self.stdout.write("  admin:        demo_admin / DemoAdmin123!")
        self.stdout.write("  manager:      demo_manager / DemoManager123!")
        self.stdout.write("  reception:    demo_reception / DemoReception123!")
        self.stdout.write("  housekeeping: demo_housekeeper / DemoHousekeeper123!")