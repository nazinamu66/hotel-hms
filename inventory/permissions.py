def is_admin(user):
    return user.is_authenticated and user.role == "ADMIN"

def is_manager(user):
    return user.is_authenticated and user.role == "MANAGER"

def is_store(user):
    return user.is_authenticated and user.role == "STORE"
