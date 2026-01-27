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
from datetime import datetime
from django.utils import timezone




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

    room = folio.room
    guest = folio.guest
    payments = folio.payments.all().order_by("collected_at")

    # 🔹 Group charges by department
    charges_by_department = defaultdict(list)
    department_totals = defaultdict(Decimal)

    for charge in folio.charges.select_related("department"):
        dept = charge.department.name
        charges_by_department[dept].append(charge)
        department_totals[dept] += charge.amount

    # 🔹 Business Profile (THIS is what you were missing)
    business = BusinessProfile.objects.first()

    context = {
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

    return render(request, "frontdesk/invoice.html", context)



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
    rooms = (
        Room.objects
        .select_related("category")
        .order_by(
            "category__name",
            "status",
            "room_number"
        )
    )

    categories = {}

    for room in rooms:
        categories.setdefault(room.category, []).append(room)

    return render(
        request,
        "frontdesk/room_board.html",
        {"categories": categories}
    )


@role_required("FRONTDESK")
def check_in(request, room_id):
    room = get_object_or_404(Room, id=room_id)

    if room.status == "OCCUPIED":
        messages.error(request, "Room is already occupied.")
        return redirect("/frontdesk/")

    if request.method == "POST":
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        phone = request.POST.get("phone")
        email = request.POST.get("email")

        if not first_name or not last_name:
            messages.error(request, "First and last name are required.")
            return redirect(request.path)

        guest = None
        if phone:
            guest = Guest.objects.filter(phone=phone).first()

        if not guest:
            guest = Guest.objects.create(
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                email=email
            )

        Folio.objects.create(
            folio_type="ROOM",
            room=room,
            guest=guest
        )

        folio.apply_daily_room_charge(charged_by=request.user)


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
    room = get_object_or_404(Room, id=room_id)

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
def checkout(request, room_id):
    room = get_object_or_404(Room, id=room_id)
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
        return redirect(f"/frontdesk/stay/{room.id}/")

    try:
        folio.check_out_at = timezone.now()
        folio.charge_room_stay(charged_by=request.user)
        folio.is_closed = True
        folio.save()

        room.status = "VACANT_DIRTY"
        room.save(update_fields=["status"])

    except Exception as e:
        messages.error(request, str(e))
        return redirect(f"/frontdesk/stay/{room.id}/")

    messages.success(
        request,
        f"Room {room.room_number} checked out successfully."
    )
    return redirect("/frontdesk/")


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def take_payment(request, room_id):
    room = get_object_or_404(Room, id=room_id)
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

        if method in ["POS", "TRANSFER"] and not reference:
            messages.error(request, "Reference is required for this payment method.")
            return redirect(request.path)

        Payment.objects.create(
            folio=folio,
            amount=amount,
            method=method,
            reference=reference,
            note=note,
            collected_by=request.user,
        )

        messages.success(request, "Payment recorded successfully.")
        return redirect(f"/frontdesk/stay/{room.id}/")

    return render(
        request,
        "frontdesk/payment.html",
        {
            "room": room,
            "folio": folio,
        }
    )
