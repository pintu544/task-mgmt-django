"""Views for the overdue service.

Three views live in this module:

* :class:`SweepView` (task 10.3) - admin-only bulk sweep of due-past tasks.
* :class:`CloseOverdueView` (task 10.4) - admin-only close of an overdue task.
* :class:`HealthView` (task 10.5) - public liveness probe.

Routing is wired in ``overdue/urls.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from rest_framework.exceptions import NotAuthenticated, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from overdue.models import Task
from overdue.permissions import IsAdmin
from overdue.services import OverdueSweeper, OverdueTransitionValidator


__all__ = ['SweepView', 'CloseOverdueView', 'HealthView']


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    """Render a datetime as a UTC ISO 8601 string with a ``Z`` suffix.

    Returns ``None`` for ``None`` inputs. Naive datetimes are interpreted
    as UTC (matching how MySQL returns ``TIMESTAMP`` columns through the
    default driver). Aware datetimes are normalized to UTC before
    formatting. Microseconds are dropped so the wire format stays stable
    across drivers.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _format_date(value: Optional[date]) -> Optional[str]:
    """Render a ``date`` as an ISO 8601 ``YYYY-MM-DD`` string, or ``None``."""

    if value is None:
        return None
    return value.isoformat()


def _serialize_task(task: Task) -> Dict[str, Any]:
    """Return the canonical task DTO shape from ``design.md``.

    Mirrors the response shape used by the Laravel_API so both services
    emit byte-compatible task envelopes. ``description`` and
    ``assignee_id`` may be ``null``; ``overdue_transitioned_at`` is ``null``
    on tasks that have never been overdue. All other keys are always
    present and non-null on a well-formed row.
    """

    return {
        'id': task.id,
        'project_id': task.project_id,
        'assignee_id': task.assignee_id,
        'title': task.title,
        'description': task.description,
        'status': task.status,
        'priority': task.priority,
        'due_date': _format_date(task.due_date),
        'overdue_transitioned_at': _format_datetime(task.overdue_transitioned_at),
        'created_at': _format_datetime(task.created_at),
        'updated_at': _format_datetime(task.updated_at),
    }


class CloseOverdueView(APIView):
    """POST /api/overdue/{task_id}/close - admin closes an overdue task.

    Authentication is required (the project default ``SanctumAuthentication``
    is inherited from ``REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']``).
    Authorization is delegated to :class:`OverdueTransitionValidator` so we
    can surface the precise difference between "not allowed by role" (403)
    and "not allowed by current state" (422) - a global ``IsAdmin``
    permission would collapse both into 403 and lose that signal.

    The transition itself runs as a conditional UPDATE
    (``WHERE id = ? AND status = 'OVERDUE'``) so two concurrent admins
    closing the same task cannot both succeed: the second update affects
    zero rows and the view returns 422 ("no longer overdue"). This keeps
    the OVERDUE -> DONE write atomic without a SELECT ... FOR UPDATE.
    """

    # Authentication is inherited from settings; no permission classes here.
    permission_classes: list = []

    def post(self, request, task_id):
        # The project ships no global ``IsAuthenticated`` default, so we
        # enforce auth explicitly here. ``SanctumAuthentication`` either
        # attaches a real ``users`` row to ``request.user`` (with
        # ``is_authenticated = True``) or raises - so an anonymous request
        # arrives with no token and falls through to this guard.
        if not getattr(request.user, 'is_authenticated', False):
            raise NotAuthenticated()

        task = Task.objects.filter(id=task_id).first()
        if task is None:
            # Fully-constructed envelope so EnvelopeRenderer passes through.
            return Response(
                {
                    'success': False,
                    'data': None,
                    'message': 'Task not found.',
                    'errors': None,
                },
                status=404,
            )

        role = getattr(request.user, 'role', None)
        result = OverdueTransitionValidator.validate(
            task.status, 'DONE', role
        )

        if not result.ok:
            return Response(
                {
                    'success': False,
                    'data': None,
                    'message': result.message,
                    'errors': None,
                },
                status=result.http_status,
            )

        # Atomic conditional update. The WHERE clause includes
        # ``status='OVERDUE'`` so we won't clobber a row that another
        # writer flipped between our SELECT above and this UPDATE.
        now = datetime.now(timezone.utc)
        affected = Task.objects.filter(id=task_id, status='OVERDUE').update(
            status='DONE',
            updated_at=now,
        )

        if affected == 0:
            # Either another admin already closed this task or some other
            # writer took the row out of OVERDUE. Either way the caller's
            # close request can no longer apply.
            return Response(
                {
                    'success': False,
                    'data': None,
                    'message': 'Task is no longer overdue.',
                    'errors': None,
                },
                status=422,
            )

        task.refresh_from_db()
        return Response(
            {
                'data': {'task': _serialize_task(task)},
                'message': 'Overdue task closed.',
            },
            status=200,
        )


class SweepView(APIView):
    """Run a single overdue sweep.

    Route: ``POST /api/overdue/sweep``. Admin-only.

    Request body (optional)::

        { "now": "2025-01-15T00:00:00Z" }

    The ``now`` field is intended for deterministic tests. When omitted,
    the server uses ``datetime.now(timezone.utc)``. Both naive and aware
    ISO 8601 datetimes are accepted; ``OverdueSweeper.sweep`` normalizes
    them to UTC.

    Response (200)::

        {
            "success": true,
            "data": {
                "transitioned": <int>,
                "transitioned_ids": [<int>, ...],
                "evaluated_at": "<iso>"
            },
            "message": "Overdue sweep completed.",
            "errors": null
        }
    """

    permission_classes = [IsAdmin]

    def post(self, request):
        now = self._parse_now(
            request.data.get('now') if hasattr(request, 'data') else None
        )
        result = OverdueSweeper.sweep(now)

        return Response(
            {
                'data': {
                    'transitioned': result.transitioned_count,
                    'transitioned_ids': result.transitioned_ids,
                    'evaluated_at': result.evaluated_at.isoformat(),
                },
                'message': 'Overdue sweep completed.',
            },
            status=200,
        )

    @staticmethod
    def _parse_now(raw):
        """Parse the optional ``now`` field, defaulting to the current UTC time.

        ``datetime.fromisoformat`` does not accept the trailing ``Z``
        timezone designator before Python 3.11, so we normalize ``Z`` to
        ``+00:00`` first. Any parsing failure is surfaced to the client as
        a 422 ValidationError with a per-field error map, matching the
        envelope contract.
        """
        if raw is None or raw == '':
            return datetime.now(timezone.utc)

        if not isinstance(raw, str):
            raise ValidationError({'now': ['Invalid ISO 8601 datetime.']})

        candidate = raw[:-1] + '+00:00' if raw.endswith('Z') else raw

        try:
            return datetime.fromisoformat(candidate)
        except (TypeError, ValueError):
            raise ValidationError({'now': ['Invalid ISO 8601 datetime.']})


class HealthView(APIView):
    """GET /api/overdue/health - public liveness probe.

    Returns ``{ ok: true }`` without touching the database. Used by
    load balancers and the deployment platform's health check.
    """

    authentication_classes: list = []  # no authentication
    permission_classes: list = []      # public

    def get(self, request):
        return Response({'data': {'ok': True}, 'message': 'ok'}, status=200)
