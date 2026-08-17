from .backend import WiFiBackend


class DevelopmentWiFiBackend(WiFiBackend):
    """
    Development backend.

    Does not communicate with real network infrastructure.
    Used for testing ERP provisioning workflows.
    """

    def create_account(self, account):
        return {
            "success": True,
            "username": account.username,
            "message": "Development account created.",
        }

    def update_account(self, account):
        return {
            "success": True,
            "username": account.username,
            "message": "Development account updated.",
        }

    def disable_account(self, account):
        return {
            "success": True,
            "username": account.username,
            "message": "Development account disabled.",
        }

    def delete_account(self, account):
        return {
            "success": True,
            "username": account.username,
            "message": "Development account deleted.",
        }

    def disconnect_session(self, session):
        return {
            "success": True,
            "session_id": session.session_id,
            "message": "Development session disconnected.",
        }

    def get_active_sessions(self):
        return []

    def get_usage(self, session):
        return {
            "upload_bytes": 0,
            "download_bytes": 0,
        }