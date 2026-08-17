import secrets
import string


def generate_wifi_username(hotel, prefix="CCH"):
    """
    Generate a short, human-friendly Wi-Fi username.

    The final uniqueness check is performed against RadiusAccount
    before saving.
    """

    hotel_code = "".join(
        word[0] for word in hotel.name.split()
    ).upper()

    hotel_code = hotel_code[:5] or prefix

    random_part = secrets.token_hex(3).upper()

    return f"{hotel_code}-{random_part}"


def generate_wifi_password(length=10):
    """
    Generate a guest-friendly Wi-Fi password.

    Avoid characters that are easily confused when read aloud.
    """

    alphabet = (
        string.ascii_letters
        + string.digits
    )

    confusing = "Il1O0"

    alphabet = "".join(
        char for char in alphabet
        if char not in confusing
    )

    return "".join(
        secrets.choice(alphabet)
        for _ in range(length)
    )