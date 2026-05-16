"""Sanctum-compatible authentication for the Django_Overdue_Service.

The Laravel_API issues bearer tokens via Laravel Sanctum. Sanctum stores
each token as a row in ``personal_access_tokens`` with a SHA-256 hex digest
of the plaintext token. The plaintext returned to the client has the form
``{id}|{plaintext}`` where ``id`` is the row id and ``plaintext`` is the
random secret.

This module implements a DRF ``BaseAuthentication`` subclass that validates
incoming bearer tokens against the same ``personal_access_tokens`` table.
The Django service never issues tokens; it only verifies them. The
``users`` row identified by ``tokenable_id`` is loaded and attached to
``request.user`` so downstream views and permissions (e.g. ``IsAdmin``)
can read ``request.user.role``.

Validation steps (any failure raises ``AuthenticationFailed``):

1. ``Authorization`` header must start with ``Bearer `` (case-insensitive
   prefix). If the header is absent, ``authenticate`` returns ``None`` so
   DRF can decide whether the route requires authentication.
2. The token after the prefix must match ``^\\d+\\|[A-Za-z0-9]+$``.
3. The ``id`` portion must identify a row in ``personal_access_tokens``.
4. ``hashlib.sha256(plaintext).hexdigest()`` must equal the stored
   ``token`` column.
5. If ``expires_at`` is set, it must not be in the past.
6. The ``tokenable_id`` must resolve to a row in ``users``.

On success, ``authenticate`` returns ``(user, None)`` per DRF's contract.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from django.db import connection
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from overdue.models import User


# ``{id}|{plaintext}`` where id is one or more digits and plaintext is a
# Sanctum-issued random alphanumeric string. Sanctum's default plaintext
# uses base62-style alphanumerics, so the character class is conservative.
_TOKEN_RE = re.compile(r'^\d+\|[A-Za-z0-9]+$')


class SanctumAuthentication(BaseAuthentication):
    """Authenticate requests using Laravel Sanctum bearer tokens.

    Reads ``Authorization: Bearer {id}|{plaintext}``, validates the token
    against ``personal_access_tokens``, and attaches the corresponding
    ``users`` row to ``request.user``.
    """

    keyword = 'Bearer'

    def authenticate(self, request):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header:
            # No credentials presented; let DRF decide via permissions.
            return None

        # Case-insensitive 'Bearer ' prefix per RFC 6750.
        prefix, _, raw_token = header.partition(' ')
        if prefix.lower() != self.keyword.lower() or not raw_token:
            # An Authorization header was provided but is not a Bearer
            # credential we understand. Treat as unauthenticated rather
            # than failed so other authenticators (if any) can attempt.
            return None

        if not _TOKEN_RE.match(raw_token):
            raise AuthenticationFailed('Invalid token.')

        token_id_str, _, plaintext = raw_token.partition('|')
        try:
            token_id = int(token_id_str)
        except ValueError:
            # Defensive: the regex already guarantees digits.
            raise AuthenticationFailed('Invalid token.')

        row = self._fetch_token_row(token_id)
        if row is None:
            raise AuthenticationFailed('Invalid token.')

        stored_hash = row['token']
        expected_hash = hashlib.sha256(plaintext.encode('utf-8')).hexdigest()
        if stored_hash != expected_hash:
            raise AuthenticationFailed('Invalid token.')

        expires_at = row['expires_at']
        if expires_at is not None and self._is_expired(expires_at):
            raise AuthenticationFailed('Token expired.')

        tokenable_id = row['tokenable_id']
        user = User.objects.filter(id=tokenable_id).first()
        if user is None:
            raise AuthenticationFailed('User not found.')

        # ``overdue.models.User`` is a plain ``models.Model`` (not an
        # AbstractBaseUser), so it does not expose ``is_authenticated`` by
        # default. DRF and downstream permissions read these attributes,
        # so set them on the instance before returning.
        user.is_authenticated = True
        user.is_anonymous = False

        return (user, None)

    def authenticate_header(self, request):
        # Returned as the WWW-Authenticate header value on 401 responses.
        return self.keyword

    @staticmethod
    def _fetch_token_row(token_id: int):
        """Return the ``personal_access_tokens`` row with id == token_id.

        Returns a dict with the columns we care about, or ``None`` if no
        matching row exists. Raw SQL is used because Django has no model
        for the Laravel-owned token table.
        """

        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT id, tokenable_id, tokenable_type, token, expires_at '
                'FROM personal_access_tokens WHERE id = %s LIMIT 1',
                [token_id],
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return {
            'id': row[0],
            'tokenable_id': row[1],
            'tokenable_type': row[2],
            'token': row[3],
            'expires_at': row[4],
        }

    @staticmethod
    def _is_expired(expires_at) -> bool:
        """Return True if the ``expires_at`` value is strictly in the past.

        MySQL DATETIME columns are returned as naive ``datetime`` objects
        by the default driver. Treat naive datetimes as UTC since Sanctum
        stores timestamps in UTC.
        """

        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return expires_at < datetime.now(timezone.utc)
        # Unknown type: be conservative and treat as not expired so we
        # don't lock users out due to a driver quirk.
        return False
