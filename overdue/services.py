"""Domain services for the overdue subsystem.

This module is the single authoritative writer for the ``OVERDUE`` task
status and the ``overdue_transitioned_at`` timestamp. The Laravel_API
must never set ``status='OVERDUE'`` or write ``overdue_transitioned_at``
directly; both columns are owned by the Django_Overdue_Service so the
overdue state machine has a single source of truth.

Two services live here:

* ``OverdueTransitionValidator`` (task 10.1) - pure validation of state
  transitions involving the ``OVERDUE`` status.
* ``OverdueSweeper`` (task 10.2) - atomic bulk transition of due-past
  tasks into ``OVERDUE``.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from django.db import transaction

from overdue.models import Task


__all__ = [
    'TransitionResult',
    'OverdueTransitionValidator',
    'SweepResult',
    'OverdueSweeper',
]


# Canonical status / role vocabularies. Kept as module-level constants so
# tests and callers can import them rather than hard-coding strings.
STATUSES = ('TODO', 'IN_PROGRESS', 'DONE', 'OVERDUE')
ROLES = ('admin', 'user')


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of a status-transition check.

    Attributes:
        ok: ``True`` if the transition is permitted, ``False`` otherwise.
        http_status: HTTP status code the caller SHOULD return. ``200``
            for an allowed transition, ``403`` for an authorization
            rejection, ``422`` for a state-machine or input-validation
            rejection.
        message: Human-readable summary suitable for the JSON envelope's
            ``message`` field.
        errors: Optional per-field error map (mirrors the envelope's
            ``errors`` slot). ``None`` for the rule-level rejections this
            validator produces today; reserved for future structured errors.
    """

    ok: bool
    http_status: int
    message: str
    errors: Optional[dict] = None


class OverdueTransitionValidator:
    """Pure-function validator for the task status state machine.

    The transition table below mirrors **Property 6: Task status state
    machine** from ``design.md`` and the corresponding requirements
    (6.2, 6.3, 6.4, 6.5, 8.1, 8.2, 8.3, 8.4):

        | current → target              | actor          | result            |
        |-------------------------------|----------------|-------------------|
        | DONE → anything               | any            | 422 (terminal)    |
        | anything → OVERDUE            | any caller     | 422 (sweep only)  |
        | OVERDUE → OVERDUE             | any            | 422 (already)     |
        | OVERDUE → IN_PROGRESS         | any            | 422               |
        | OVERDUE → TODO                | any            | 422               |
        | OVERDUE → DONE                | admin          | 200 (allow)       |
        | OVERDUE → DONE                | user (member)  | 403               |
        | TODO/IN_PROGRESS → TODO/IN_PROGRESS/DONE | any | 200 (allow)      |
        | unknown current or target     | any            | 422               |

    The class performs no I/O and has no state. It is intended to be
    composed by views (e.g. ``CloseOverdueView``) that supply the HTTP
    response wrapping and any persistence.
    """

    @staticmethod
    def validate(current: str, target: str, role: str) -> TransitionResult:
        """Decide whether ``current → target`` is permitted for ``role``.

        Args:
            current: The task's current ``status`` value. Expected to be
                one of :data:`STATUSES`; unknown values yield a 422 result.
            target: The desired ``status`` value. Expected to be one of
                :data:`STATUSES`; unknown values yield a 422 result.
            role: The acting user's role. Expected to be one of
                :data:`ROLES`. Any value other than ``'admin'`` is treated
                as a non-admin (member) for authorization purposes.

        Returns:
            A :class:`TransitionResult` describing whether the transition
            is permitted and, if not, the HTTP status and message the
            caller SHOULD surface to the client.
        """

        # 1. Defensive input validation. Unknown statuses are rejected with
        #    422 so the caller can surface a clear error to the client.
        if current not in STATUSES:
            return TransitionResult(
                ok=False,
                http_status=422,
                message=f"Unknown current status '{current}'.",
            )
        if target not in STATUSES:
            return TransitionResult(
                ok=False,
                http_status=422,
                message=f"Unknown target status '{target}'.",
            )

        # 2. DONE is terminal: no transition out of DONE is permitted by
        #    any actor. Checked before the target-side rules so that
        #    DONE → OVERDUE / DONE → DONE / etc. all receive the same,
        #    most-specific message.
        if current == 'DONE':
            return TransitionResult(
                ok=False,
                http_status=422,
                message=(
                    'Done is terminal; status cannot be changed once a '
                    'task is marked DONE.'
                ),
            )

        # 3. Only the Django sweep may set status to OVERDUE. Any
        #    caller-initiated transition into OVERDUE is rejected.
        if target == 'OVERDUE':
            if current == 'OVERDUE':
                return TransitionResult(
                    ok=False,
                    http_status=422,
                    message='Task is already overdue.',
                )
            return TransitionResult(
                ok=False,
                http_status=422,
                message='Only the overdue sweep may set status to OVERDUE.',
            )

        # 4. Transitions out of OVERDUE follow strict rules.
        if current == 'OVERDUE':
            if target == 'IN_PROGRESS':
                return TransitionResult(
                    ok=False,
                    http_status=422,
                    message='Overdue tasks cannot move back to in progress.',
                )
            if target == 'TODO':
                return TransitionResult(
                    ok=False,
                    http_status=422,
                    message=(
                        'Overdue tasks cannot revert to TODO; close the '
                        'task or contact an Admin.'
                    ),
                )
            # target == 'DONE' (the only remaining option after the
            # checks above). Admin-only.
            if role == 'admin':
                return TransitionResult(
                    ok=True,
                    http_status=200,
                    message='Overdue task closed.',
                )
            return TransitionResult(
                ok=False,
                http_status=403,
                message='Only Admins may close overdue tasks.',
            )

        # 5. From TODO or IN_PROGRESS, transitions to TODO / IN_PROGRESS
        #    / DONE are permitted (target == 'OVERDUE' was rejected at
        #    step 3 above).
        return TransitionResult(
            ok=True,
            http_status=200,
            message='Status transition allowed.',
        )


@dataclass
class SweepResult:
    """Summary of a single ``OverdueSweeper.sweep`` invocation.

    Attributes:
        transitioned_count: Number of task rows moved to ``OVERDUE``.
        transitioned_ids: Identifiers of every task that transitioned,
            in the order they were observed by the pre-sweep scan.
        evaluated_at: The (UTC, tz-aware) timestamp used both as the
            comparison anchor for ``due_date`` and as the value written
            to ``overdue_transitioned_at`` on every transitioned row.
    """

    transitioned_count: int
    transitioned_ids: List[int] = field(default_factory=list)
    evaluated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class OverdueSweeper:
    """Atomically transitions due-past tasks to ``OVERDUE``.

    This class is the only authoritative writer for ``status='OVERDUE'``
    and ``overdue_transitioned_at`` in the shared ``tasks`` table. The
    sweep is executed inside a single database transaction so the read
    of the predicate set and the bulk update are consistent: no row can
    be observed in the predicate set and then escape the update because
    of a concurrent writer.

    Tasks already in status ``DONE`` are intentionally skipped even when
    their ``due_date`` is past, because ``DONE`` is terminal in the
    domain state machine (Requirement 7.2). Tasks already in
    ``OVERDUE`` are also skipped because the predicate filters by
    ``status IN ('TODO', 'IN_PROGRESS')`` only, which keeps the sweep
    idempotent across repeated invocations.
    """

    @classmethod
    def sweep(cls, now: datetime) -> SweepResult:
        """Run a single sweep pass against the ``tasks`` table.

        Args:
            now: The wall-clock instant against which ``due_date`` is
                compared. Naive datetimes are interpreted as UTC; aware
                datetimes are normalized to UTC before any comparison.
                The same instant is stamped into
                ``overdue_transitioned_at`` for every transitioned row,
                so callers using a fixed ``now`` get deterministic
                output.

        Returns:
            A ``SweepResult`` describing the transition. ``transitioned_ids``
            is empty (and ``transitioned_count`` is zero) when the
            predicate set is empty. The ``evaluated_at`` field always
            holds the UTC-normalized ``now``.
        """
        # Normalize ``now`` to UTC. Naive inputs are treated as UTC so
        # callers cannot accidentally cause a date-boundary shift by
        # passing a naive local-time value.
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        today = now.date()

        # The predicate scan and the bulk UPDATE share a single
        # transaction. This keeps the response (transitioned_ids)
        # consistent with the rows that were actually mutated.
        with transaction.atomic():
            ids_qs = (
                Task.objects
                .filter(
                    due_date__lt=today,
                    status__in=['TODO', 'IN_PROGRESS'],
                )
                .values_list('id', flat=True)
            )
            transitioned_ids: List[int] = list(ids_qs)

            if transitioned_ids:
                # Re-query by id-set so the WHERE clause of the UPDATE
                # is unambiguous on MySQL. ``updated_at`` is bumped to
                # match the transition instant, which mirrors how
                # Laravel's Eloquent timestamps would behave on a write.
                Task.objects.filter(id__in=transitioned_ids).update(
                    status='OVERDUE',
                    overdue_transitioned_at=now,
                    updated_at=now,
                )

        return SweepResult(
            transitioned_count=len(transitioned_ids),
            transitioned_ids=transitioned_ids,
            evaluated_at=now,
        )
