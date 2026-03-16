from django.shortcuts import render
from accounts.decorators import role_required
from core.utils import get_user_hotel

from core.services.dashboard.manager_dashboard import get_manager_dashboard
from core.services.dashboard.director_dashboard import get_director_dashboard
from core.services.dashboard.accountant_dashboard import get_accountant_dashboard


@role_required("DIRECTOR","MANAGER","ADMIN","ACCOUNTANT")
def hotel_dashboard(request):

    user = request.user
    hotel = get_user_hotel(user)

    if user.role == "DIRECTOR":
        context = get_director_dashboard()

    elif user.role == "MANAGER":
        context = get_manager_dashboard(hotel)

    elif user.role == "ACCOUNTANT":
        context = get_accountant_dashboard(hotel)

    else:
        context = get_manager_dashboard(hotel)

    return render(
        request,
        "dashboard/hotel_dashboard.html",
        context
    )