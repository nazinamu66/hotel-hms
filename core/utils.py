def get_user_hotel(user):

    if user.role == "DIRECTOR":
        return None  # global access

    if user.hotel:
        return user.hotel

    if user.department:
        return user.department.hotel

    return None