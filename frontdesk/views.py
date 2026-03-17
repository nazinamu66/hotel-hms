from accounts.decorators import role_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from billing.models import Payment
from itertools import groupby
from rooms.models import Room, RoomCategory
from django.db import transaction
from billing.models import Guest, Folio
from collections import defaultdict
from decimal import Decimal
from core.models import BusinessProfile
from django.utils import timezone
from billing.models import Guest, Reservation
from django.db.models import Q
from frontdesk.services import get_available_rooms
from datetime import timedelta, datetime
from maintenance.models import MaintenanceTicket




@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_search(request):

    hotel = request.user.department.hotel
    query = request.GET.get("q", "").strip()

    guests = Guest.objects.filter(hotel=hotel)

    if query:
        guests = guests.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query) |
            Q(id_number__icontains=query)
        )

    guests = guests.order_by("-created_at")[:50]

    return render(
        request,
        "frontdesk/guest_search.html",
        {
            "guests": guests,
            "query": query,
        }
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_profile(request, guest_id):

    guest = get_object_or_404(
        Guest,
        id=guest_id,
        hotel=request.user.department.hotel
    )

    folios = (
        Folio.objects
        .filter(guest=guest)
        .select_related("room")
        .order_by("-created_at")
    )

    total_spent = sum(f.total_charges for f in folios)

    active_folio = folios.filter(is_closed=False).first()

    context = {
        "guest": guest,
        "folios": folios,
        "active_folio": active_folio,
        "total_spent": total_spent
    }

    return render(
        request,
        "frontdesk/guest_profile.html",
        context
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_create(request):

    hotel = request.user.department.hotel

    if request.method == "POST":

        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        phone = request.POST.get("phone")
        email = request.POST.get("email")
        nationality = request.POST.get("nationality")
        id_number = request.POST.get("id_number")

        if not first_name or not last_name:
            messages.error(request, "First and last name are required.")
            return redirect(request.path)

        guest = Guest.objects.create(
            hotel=hotel,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            nationality=nationality,
            id_number=id_number
        )

        messages.success(request, "Guest created successfully.")

        return redirect("frontdesk_guest_profile", guest.id)

    return render(
        request,
        "frontdesk/guest_create.html"
    )


def build_invoice_context(folio):
    room = folio.room
    guest = folio.guest
    payments = folio.payments.all().order_by("collected_at")

    charges_by_department = defaultdict(list)
    department_totals = defaultdict(Decimal)

    for charge in folio.charges.select_related("department"):
        dept = charge.department.name
        charges_by_department[dept].append(charge)
        department_totals[dept] += charge.amount

    business = BusinessProfile.objects.first()

    return {
        "business": business,
        "folio": folio,
        "room": room,
        "guest": guest,
        "payments": payments,
        "charges_by_department": dict(charges_by_department),
        "department_totals": dict(department_totals),
        "total_charges": folio.total_charges,
        "total_payments": folio.total_payments,
        "balance": folio.balance,
    }


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def invoice_pdf_view(request, folio_id):
    folio = get_object_or_404(Folio, id=folio_id)

    context = build_invoice_context(folio)  # reuse logic

    return render(request, "frontdesk/invoice_pdf.html", context)


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def invoice_view(request, folio_id):

    folio = get_object_or_404(Folio, id=folio_id)
    context = build_invoice_context(folio)

    return render(
        request,
        "frontdesk/invoice.html",
        context
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def folio_invoice(request, folio_id):
    folio = get_object_or_404(Folio, id=folio_id)

    grouped = defaultdict(lambda: {
        "charges": [],
        "subtotal": Decimal("0.00")
    })

    for charge in folio.charges.select_related("department"):
        dept = charge.department.name
        grouped[dept]["charges"].append(charge)
        grouped[dept]["subtotal"] += charge.amount

    context = {
        "folio": folio,
        "room": folio.room,
        "guest": folio.guest,
        "grouped_charges": dict(grouped),
        "payments": folio.payments.all().order_by("collected_at"),
        "total_charges": folio.total_charges,
        "total_payments": folio.total_payments,
        "balance": folio.balance,
    }

    return render(
        request,
        "frontdesk/invoice.html",
        context
    )


@role_required("FRONTDESK", "MANAGER")
def room_board(request):

    hotel = request.user.department.hotel

    rooms = (
        Room.objects
        .filter(hotel=hotel)
        .select_related("category")
        .order_by("category__name", "room_number")
    )

    today = timezone.now().date()
    tomorrow = today + timedelta(days=1)

    expected_departures_today = set(
        Folio.objects.filter(
            hotel=hotel,
            is_closed=False,
            check_out_at__date=today
        ).values_list("room_id", flat=True)
    )

    # Reservations
    reservations_today = set(
        Reservation.objects.filter(
            hotel=hotel,
            check_in_date=today,
            status="RESERVED"
        ).values_list("room_id", flat=True)
    )

    arrivals_tomorrow = set(
        Reservation.objects.filter(
            hotel=hotel,
            check_in_date=tomorrow,
            status="RESERVED"
        ).values_list("room_id", flat=True)
    )

    # Active folios
    active_folios = Folio.objects.filter(
        hotel=hotel,
        is_closed=False
    ).select_related("guest", "room")

    folio_map = {f.room_id: f for f in active_folios}

    # Maintenance rooms
    maintenance_rooms = set(
        MaintenanceTicket.objects.exclude(
            status="RESOLVED"
        ).values_list("room_id", flat=True)
    )

    stats = {
        "available": rooms.filter(status="AVAILABLE").count(),
        "occupied": rooms.filter(status="OCCUPIED").count(),
        "dirty": rooms.filter(
            status__in=["VACANT_DIRTY", "OCCUPIED_DIRTY"]
        ).count(),
        "arrivals": len(reservations_today),
        "departures": Folio.objects.filter(
            hotel=hotel,
            check_out_at__date=today,
            is_closed=False
        ).count()
    }

    categories = {}

    for room in rooms:
        categories.setdefault(room.category, []).append(room)

    return render(
        request,
        "frontdesk/room_board.html",
        {
            "categories": categories,
            "stats": stats,
            "folio_map": folio_map,
            "reservations_today": reservations_today,
            "arrivals_tomorrow": arrivals_tomorrow,
            "maintenance_rooms": maintenance_rooms,
            "expected_departures_today": expected_departures_today,
            "today": today
        }
    )


@role_required("FRONTDESK")
@transaction.atomic
def check_in(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    # Prevent check-in if room is occupied
    if room.status in ["OCCUPIED", "OCCUPIED_DIRTY"]:
        messages.error(request, "Room is already occupied.")
        return redirect("/frontdesk/")

    if request.method == "POST":

        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        phone = request.POST.get("phone")
        email = request.POST.get("email")

        nationality = request.POST.get("nationality")
        id_number = request.POST.get("id_number")
        expected_checkout = request.POST.get("expected_checkout")

        if not first_name or not last_name:
            messages.error(request, "First and last name are required.")
            return redirect(request.path)

        guest = None

        # 🔎 Try find returning guest
        if phone:
            guest = Guest.objects.filter(
                phone=phone,
                hotel=room.hotel
            ).first()

        # 🆕 Create guest if not found
        if not guest:
            guest = Guest.objects.create(
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                email=email,
                nationality=nationality,
                id_number=id_number,
                hotel=room.hotel
            )
        else:
            # Update guest info if changed
            guest.first_name = first_name
            guest.last_name = last_name
            guest.email = email
            guest.nationality = nationality
            guest.id_number = id_number
            guest.save()

        # 🔐 Ensure no active stay exists
        existing = Folio.get_active_room_folio(room)

        if existing:
            messages.error(request, "Room already has an active stay.")
            return redirect("/frontdesk/")

        # 📂 Create folio
        folio = Folio.objects.create(
            folio_type="ROOM",
            room=room,
            guest=guest,
            hotel=room.hotel
        )

        # Optional expected checkout
        if expected_checkout:
            folio.check_out_at = expected_checkout
            folio.save(update_fields=["check_out_at"])

        # 💰 Apply first night charge
        try:
            folio.apply_daily_room_charge(charged_by=request.user)
        except Exception as e:
            messages.error(request, str(e))
            return redirect("/frontdesk/")

        # 🛏 Update room status
        room.refresh_status()

        messages.success(
            request,
            f"{guest.full_name} checked into Room {room.room_number}"
        )

        return redirect("/frontdesk/")

    return render(
        request,
        "frontdesk/check_in.html",
        {"room": room}
    )


@role_required('FRONTDESK', 'MANAGER', 'ADMIN')
def active_stay(request, room_id):
    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    folio = Folio.objects.filter(
        folio_type='ROOM',
        room=room,
        is_closed=False
    ).first()

    if not folio:
        messages.error(request, "No active stay for this room.")
        return redirect('/frontdesk/')

    guest = folio.guest

    if not guest:
        messages.warning(
            request,
            "This stay has no guest record. Please update guest details."
        )

    # ================= TIMELINE =================
    timeline = []

    # 🟢 Check-in event
    timeline.append({
        "time": folio.created_at,
        "type": "checkin",
        "label": "Check-in",
        "description": f"{guest.first_name} {guest.last_name} checked in",
        "amount": None,
    })

    # 🍽 Charges
    for charge in folio.charges.select_related("department"):
        timeline.append({
            "time": charge.created_at,
            "type": "charge",
            "label": charge.department.name,
            "description": charge.description,
            "amount": charge.amount,
        })

    # 💳 Payments
    for payment in folio.payments.all():
        timeline.append({
            "time": payment.collected_at,
            "type": "payment",
            "label": payment.method,
            "description": "Payment received",
            "amount": -payment.amount,
        })

    # 🚪 Checkout (if closed)
    if folio.is_closed:
        timeline.append({
            "time": folio.updated_at if hasattr(folio, "updated_at") else folio.created_at,
            "type": "checkout",
            "label": "Checkout",
            "description": "Guest checked out",
            "amount": None,
        })

    # 🔽 Newest → Oldest
    timeline.sort(key=lambda x: x["time"], reverse=True)

    context = {
        "room": room,
        "folio": folio,
        "guest": guest,
        "timeline": timeline,
        "payments": folio.payments.all(),
        "can_force_checkout": request.user.role in ["MANAGER", "ADMIN"],
    }

    return render(
        request,
        "frontdesk/active_stay.html",
        context
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def extend_stay(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    folio = Folio.get_active_room_folio(room)

    if not folio:
        messages.error(request, "No active stay found.")
        return redirect("/frontdesk/")

    if request.method == "POST":

        new_date = request.POST.get("new_checkout")

        if not new_date:
            messages.error(request, "Checkout date required.")
            return redirect(request.path)

        folio.check_out_at = new_date
        folio.save(update_fields=["check_out_at"])

        messages.success(
            request,
            f"Stay extended until {new_date}"
        )

        return redirect(f"/frontdesk/stay/{room.id}/")

    return render(
        request,
        "frontdesk/extend_stay.html",
        {
            "room": room,
            "folio": folio
        }
    )
@role_required("FRONTDESK", "MANAGER", "ADMIN")
@transaction.atomic
def change_room(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    folio = Folio.get_active_room_folio(room)

    if not folio:
        messages.error(request, "No active stay found.")
        return redirect("/frontdesk/")

    # rooms that can receive guest
    available_rooms = Room.objects.filter(
        hotel=room.hotel,
        status="AVAILABLE"
    ).exclude(id=room.id)

    if request.method == "POST":

        new_room_id = request.POST.get("new_room")

        if not new_room_id:
            messages.error(request, "Please select a room.")
            return redirect(request.path)

        new_room = get_object_or_404(
            Room,
            id=new_room_id,
            hotel=room.hotel
        )

        if new_room.status != "AVAILABLE":
            messages.error(request, "Selected room is not available.")
            return redirect(request.path)

        old_room = folio.room

        # move folio
        folio.room = new_room
        folio.save(update_fields=["room"])

        # update statuses
        old_room.status = "VACANT_DIRTY"
        old_room.save(update_fields=["status"])

        new_room.refresh_status()

        messages.success(
            request,
            f"Guest moved from Room {old_room.room_number} to {new_room.room_number}"
        )

        return redirect(f"/frontdesk/stay/{new_room.id}/")

    return render(
        request,
        "frontdesk/change_room.html",
        {
            "room": room,
            "folio": folio,
            "available_rooms": available_rooms
        }
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def checkout(request, room_id):
    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )
    folio = Folio.get_active_room_folio(room)

    if not folio:
        messages.error(request, "No active stay found.")
        return redirect("/frontdesk/")

    # 🔐 Normal staff cannot checkout with balance
    if folio.balance != 0 and request.user.role == "FRONTDESK":
        messages.error(
            request,
            "Outstanding balance. Manager approval required."
        )
        return redirect("frontdesk_active_stay", room.id)

    try:
        folio.check_out_at = timezone.now()
        folio.charge_room_stay(charged_by=request.user)
        folio.is_closed = True
        folio.save()

        room.status = "VACANT_DIRTY"
        room.save(update_fields=["status"])

    except Exception as e:
        messages.error(request, str(e))
        return redirect("frontdesk_active_stay", room.id)

    messages.success(
        request,
        f"Room {room.room_number} checked out successfully."
    )
    return redirect("/frontdesk/")


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def take_payment(request, room_id):
    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )
    folio = Folio.get_active_room_folio(room)

    if not folio:
        messages.error(request, "No active stay found.")
        return redirect("/frontdesk/")

    if request.method == "POST":
        amount = Decimal(request.POST.get("amount", "0"))
        method = request.POST.get("method")
        reference = request.POST.get("reference", "")
        note = request.POST.get("note", "")

        if amount <= 0:
            messages.error(request, "Invalid payment amount.")
            return redirect(request.path)

        if amount > folio.balance:
            messages.error(request, "Payment exceeds outstanding balance.")
            return redirect(request.path)

        if method in ["POS", "TRANSFER"] and not reference:
            messages.error(request, "Reference is required for this payment method.")
            return redirect(request.path)

        payment = Payment.objects.create(
            folio=folio,
            amount=amount,
            method=method,
            reference=reference,
            note=note,
            collected_by=request.user,
        )

        # 🔥 ACCOUNTING HOOK
        try:
            from accounting.services.postings.payment import post_payment
            post_payment(payment)
        except Exception as e:
            print("Accounting error (payment):", e)

        messages.success(request, "Payment recorded successfully.")
        return redirect("frontdesk_active_stay", room.id)

    return render(
        request,
        "frontdesk/payment.html",
        {
            "room": room,
            "folio": folio,
        }
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR", "FRONTDESK")
@transaction.atomic
def night_audit(request):

    hotel = request.user.department.hotel

    if request.method == "POST":

        active_folios = Folio.objects.filter(
            folio_type="ROOM",
            hotel=hotel,
            is_closed=False
        ).select_related("room", "room__category")

        charged = 0

        for folio in active_folios:

            before = folio.last_room_charge_date

            folio.apply_daily_room_charge(charged_by=request.user)

            if folio.last_room_charge_date != before:
                charged += 1

        messages.success(
            request,
            f"Night Audit complete. {charged} room charges applied."
        )

        return redirect("frontdesk_room_board")

    return render(
        request,
        "frontdesk/night_audit.html"
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_list(request):

    hotel = request.user.department.hotel

    reservations = (
        Reservation.objects
        .filter(hotel=hotel)
        .select_related("guest", "room_category")
        .order_by("-created_at")
    )

    return render(
        request,
        "frontdesk/reservations.html",
        {"reservations": reservations}
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def create_reservation(request):

    hotel = request.user.department.hotel

    # Categories that exist in this hotel
    categories = (
        RoomCategory.objects
        .filter(rooms__hotel=hotel)
        .distinct()
        .order_by("name")
    )

    if request.method == "POST":

        guest_id = request.POST.get("guest")
        category_id = request.POST.get("category")
        check_in = request.POST.get("check_in")
        check_out = request.POST.get("check_out")

        if not guest_id or not category_id:
            messages.error(request, "Guest and room category required.")
            return redirect(request.path)

        guest = get_object_or_404(
            Guest,
            id=guest_id,
            hotel=hotel
        )

        category = get_object_or_404(
            RoomCategory,
            id=category_id
        )

        # Check availability
        available_rooms = get_available_rooms(
            hotel,
            category,
            check_in,
            check_out
        )

        if not available_rooms.exists():
            messages.error(
                request,
                "No rooms available for selected dates."
            )
            return redirect(request.path)

        Reservation.objects.create(
            guest=guest,
            hotel=hotel,
            room_category=category,
            check_in_date=check_in,
            check_out_date=check_out,
            created_by=request.user
        )

        messages.success(request, "Reservation created.")

        return redirect("frontdesk_reservations")

    guests = (
        Guest.objects
        .filter(hotel=hotel)
        .order_by("-created_at")[:50]
    )

    return render(
        request,
        "frontdesk/create_reservation.html",
        {
            "guests": guests,
            "categories": categories
        }
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_checkin(request, reservation_id):

    reservation = get_object_or_404(Reservation, id=reservation_id)

    if reservation.status != "RESERVED":
        messages.error(request, "Reservation not valid.")
        return redirect("frontdesk_reservations")


    available_rooms = get_available_rooms(
        reservation.hotel,
        reservation.room_category,
        reservation.check_in_date,
        reservation.check_out_date
    )

    available_room = available_rooms.first()

    if not available_room:
        messages.error(request, "No room available.")
        return redirect("frontdesk_reservations")

    folio = Folio.objects.create(
        folio_type="ROOM",
        guest=reservation.guest,
        room=available_room,
        hotel=reservation.hotel
    )

    reservation.room = available_room
    reservation.status = "CHECKED_IN"
    reservation.save(update_fields=["room", "status"])

    available_room.refresh_status()

    messages.success(request, "Guest checked in.")

    return redirect("frontdesk_active_stay", available_room.id)



@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_calendar(request):

    hotel = request.user.department.hotel

    # -------------------------
    # Filters
    # -------------------------
    start = request.GET.get("start")
    category_id = request.GET.get("category")
    room_number = request.GET.get("room")

    if start:
        try:
            start_date = datetime.strptime(start[:10], "%Y-%m-%d").date()
        except ValueError:
            start_date = timezone.now().date()
    else:
        start_date = timezone.now().date()

    days = [start_date + timedelta(days=i) for i in range(14)]

    prev_week = start_date - timedelta(days=7)
    next_week = start_date + timedelta(days=7)

    # -------------------------
    # Rooms Query
    # -------------------------
    rooms = Room.objects.filter(hotel=hotel).select_related("category")

    if category_id:
        rooms = rooms.filter(category_id=category_id)

    if room_number:
        rooms = rooms.filter(room_number__icontains=room_number)

    rooms = rooms.order_by("room_number")

    # -------------------------
    # Reservations
    # -------------------------
    reservations = Reservation.objects.filter(
        hotel=hotel,
        status="RESERVED"
    )

    calendar = []

    for room in rooms:

        row = {
            "room": room,
            "days": []
        }

        for day in days:

            reservation = reservations.filter(
                room=room,
                check_in_date__lte=day,
                check_out_date__gt=day
            ).first()

            row["days"].append({
                "date": day,
                "reservation": reservation,
                "room_id": room.id
            })

        calendar.append(row)

    categories = (
        RoomCategory.objects
        .filter(rooms__hotel=hotel)
        .distinct()
        .order_by("name")
    )

    context = {
        "calendar": calendar,
        "days": days,
        "start_date": start_date,
        "prev_week": prev_week,
        "next_week": next_week,
        "categories": categories,
        "filters": request.GET
    }

    return render(
        request,
        "frontdesk/reservation_calendar.html",
        context
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def calendar_create_reservation(request):

    hotel = request.user.department.hotel

    room_id = request.GET.get("room")
    check_in = request.GET.get("date")

    room = get_object_or_404(Room, id=room_id, hotel=hotel)

    guests = Guest.objects.filter(hotel=hotel)[:50]

    if request.method == "POST":

        guest_id = request.POST.get("guest")
        check_out = request.POST.get("check_out")

        guest = get_object_or_404(Guest, id=guest_id, hotel=hotel)

        Reservation.objects.create(
            guest=guest,
            hotel=hotel,
            room=room,
            room_category=room.category,
            check_in_date=check_in,
            check_out_date=check_out,
            created_by=request.user
        )

        messages.success(request, "Reservation created.")

        return redirect("frontdesk_reservation_calendar")

    return render(
        request,
        "frontdesk/calendar_reservation.html",
        {
            "room": room,
            "check_in": check_in,
            "guests": guests
        }
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def arrivals_departures_today(request):

    hotel = request.user.department.hotel
    today = timezone.now().date()

    arrivals = Reservation.objects.filter(
        hotel=hotel,
        check_in_date=today,
        status="RESERVED"
    ).select_related("guest", "room_category")

    departures = Folio.objects.filter(
        hotel=hotel,
        check_out_at__date=today,
        is_closed=False
    ).select_related("guest", "room")

    in_house = Folio.objects.filter(
        hotel=hotel,
        is_closed=False
    ).select_related("guest", "room")

    return render(
        request,
        "frontdesk/today_board.html",
        {
            "arrivals": arrivals,
            "departures": departures,
            "in_house": in_house
        }
    )