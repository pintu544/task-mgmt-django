"""Custom DRF exception handler that emits the project JSON envelope.

The status-to-message mapping mirrors the Laravel ``App\\Exceptions\\Handler``
implementation so both services produce byte-compatible error payloads:

    | Exception                           | HTTP | message                       |
    |-------------------------------------|------|-------------------------------|
    | ValidationError                     | 422  | "The given data was invalid." |
    | NotAuthenticated / AuthenticationFailed | 401 | "Authentication required."    |
    | PermissionDenied                    | 403  | "Insufficient permissions."   |
    | NotFound                            | 404  | "Resource not found."         |
    | MethodNotAllowed                    | 405  | "Method not allowed."         |
    | unhandled                           | 500  | "An unexpected error occurred." |
    | other 4xx/5xx with response         | as-is| str(exc)                      |

The handler returns a ``Response`` whose body is a fully-constructed envelope
(``success`` + ``data`` keys present), which the ``EnvelopeRenderer``
detects and passes through unchanged.
"""

from __future__ import annotations

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def _envelope(message, errors=None, data=None):
    return {
        'success': False,
        'data': data,
        'message': message,
        'errors': errors,
    }


def envelope_exception_handler(exc, context):
    """DRF exception handler that maps to the shared JSON envelope."""

    response = exception_handler(exc, context)

    if response is None:
        # Unhandled exception — DRF returns None so the default handler can
        # bubble it to Django. Convert to a 500 envelope so the response shape
        # is preserved across the wire.
        return Response(
            _envelope('An unexpected error occurred.'),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Validation errors — DRF leaves the per-field error map in ``response.data``.
    if isinstance(exc, exceptions.ValidationError):
        return Response(
            _envelope(
                'The given data was invalid.',
                errors=response.data if isinstance(response.data, dict) else None,
            ),
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        return Response(
            _envelope('Authentication required.'),
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, exceptions.PermissionDenied):
        return Response(
            _envelope('Insufficient permissions.'),
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, exceptions.NotFound):
        return Response(
            _envelope('Resource not found.'),
            status=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, exceptions.MethodNotAllowed):
        return Response(
            _envelope('Method not allowed.'),
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # Other handled 4xx / 5xx — preserve the original status and use the
    # exception's string form as the message. Carry through any field-error
    # dict that DRF placed in ``response.data``.
    errors = response.data if isinstance(response.data, dict) else None
    return Response(
        _envelope(str(exc), errors=errors),
        status=response.status_code,
    )
