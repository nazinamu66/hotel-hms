from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.services.access import get_accessible_hotels

from .forms import OpeningStockBatchForm, OpeningStockLineForm
from .models import OpeningStockBatch, OpeningStockLine
from .services import post_opening_stock_batch


OPENING_ROLES = ("ADMIN", "DIRECTOR", "GENERAL_MANAGER", "MANAGER", "CHIEF_ACCOUNTANT", "ACCOUNTANT")


@role_required(*OPENING_ROLES)
def opening_stock_dashboard(request):
    hotels = get_accessible_hotels(request.user)
    if not hotels.exists():
        raise PermissionDenied

    selected_hotel_id = request.POST.get("hotel") if request.method == "POST" else request.GET.get("hotel")
    if selected_hotel_id:
        hotel = get_object_or_404(hotels, pk=selected_hotel_id)
    elif hotels.count() == 1:
        hotel = hotels.first()
    else:
        hotel = None

    selected_batch = None
    if hotel and request.GET.get("batch"):
        selected_batch = get_object_or_404(
            OpeningStockBatch.objects.prefetch_related("lines"),
            pk=request.GET["batch"], hotel=hotel,
        )

    batch_form = OpeningStockBatchForm()
    line_form = OpeningStockLineForm(hotel=hotel)

    if request.method == "POST":
        action = request.POST.get("action")
        if hotel is None:
            messages.error(request, "Select a hotel first.")
            return redirect("opening_stock:dashboard")

        if action == "CREATE_BATCH":
            batch_form = OpeningStockBatchForm(request.POST)
            if batch_form.is_valid():
                batch = batch_form.save(commit=False)
                batch.hotel = hotel
                batch.created_by = request.user
                batch.save()
                messages.success(request, "Opening balance batch created. Add its stock, linen and equipment lines.")
                return redirect(f"/opening-stock/?hotel={hotel.pk}&batch={batch.pk}")
            messages.error(request, "Please correct the opening batch details.")

        elif action == "ADD_LINE":
            batch = get_object_or_404(
                OpeningStockBatch, pk=request.POST.get("batch_id"),
                hotel=hotel, status="DRAFT",
            )
            line_form = OpeningStockLineForm(
                request.POST,
                hotel=hotel,
                instance=OpeningStockLine(batch=batch),
            )
            if line_form.is_valid():
                line = line_form.save(commit=False)
                line.batch = batch
                try:
                    line.full_clean()
                    line.save()
                except ValidationError as exc:
                    line_form.add_error(None, " ".join(exc.messages))
                else:
                    messages.success(request, "Opening balance line added.")
                    return redirect(f"/opening-stock/?hotel={hotel.pk}&batch={batch.pk}")
            selected_batch = batch
            messages.error(request, "Please correct the opening line details.")

        elif action == "DELETE_LINE":
            batch = get_object_or_404(
                OpeningStockBatch, pk=request.POST.get("batch_id"),
                hotel=hotel, status="DRAFT",
            )
            line = get_object_or_404(batch.lines, pk=request.POST.get("line_id"))
            line.delete()
            messages.success(request, "Draft opening balance line removed.")
            return redirect(f"/opening-stock/?hotel={hotel.pk}&batch={batch.pk}")

        elif action == "POST_BATCH":
            batch = get_object_or_404(
                OpeningStockBatch, pk=request.POST.get("batch_id"), hotel=hotel,
            )
            try:
                post_opening_stock_batch(batch=batch, user=request.user)
            except (ValidationError, ValueError) as exc:
                messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            else:
                batch.refresh_from_db()
                messages.success(request, f"Opening balances posted successfully. Journal: {batch.journal_entry_id}.")
            return redirect(f"/opening-stock/?hotel={hotel.pk}&batch={batch.pk}")

    batches = (
        OpeningStockBatch.objects.filter(hotel=hotel)
        .select_related("created_by", "posted_by")
        .order_by("-opening_date", "-id")
        if hotel else OpeningStockBatch.objects.none()
    )

    return render(request, "opening_stock/dashboard.html", {
        "hotels": hotels,
        "selected_hotel": hotel,
        "batches": batches,
        "selected_batch": selected_batch,
        "batch_form": batch_form,
        "line_form": line_form,
    })
