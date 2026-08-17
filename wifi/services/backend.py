from abc import ABC, abstractmethod


class WiFiBackendError(Exception):
    """Base exception for Wi-Fi backend errors."""


class WiFiBackend(ABC):
    """
    Abstract interface for Wi-Fi authentication backends.

    The ERP communicates with this interface rather than
    directly with FreeRADIUS, MikroTik, or another vendor.
    """

    @abstractmethod
    def create_account(self, account):
        """
        Create or provision an authentication account.
        """
        raise NotImplementedError

    @abstractmethod
    def update_account(self, account):
        """
        Update an existing authentication account.
        """
        raise NotImplementedError

    @abstractmethod
    def disable_account(self, account):
        """
        Disable authentication for an account.
        """
        raise NotImplementedError

    @abstractmethod
    def delete_account(self, account):
        """
        Remove an authentication account from the backend.
        """
        raise NotImplementedError

    @abstractmethod
    def disconnect_session(self, session):
        """
        Forcefully terminate an active session.
        """
        raise NotImplementedError

    @abstractmethod
    def get_active_sessions(self):
        """
        Return active sessions reported by the backend.
        """
        raise NotImplementedError

    @abstractmethod
    def get_usage(self, session):
        """
        Return usage/accounting information for a session.
        """
        raise NotImplementedError