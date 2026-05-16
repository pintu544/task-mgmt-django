"""DRF permissions for the overdue service."""

from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    """Allow access only to authenticated users whose role is 'admin'.

    Authentication is performed by overdue.auth.SanctumAuthentication, which
    attaches a User instance with is_authenticated=True and a string `role`
    attribute (`admin` or `user`) to request.user.
    """

    message = 'Insufficient permissions.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if user is None:
            return False
        if not getattr(user, 'is_authenticated', False):
            return False
        return getattr(user, 'role', None) == 'admin'
