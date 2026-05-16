"""Unmanaged Django models for the overdue service.

These models mirror the Laravel-owned MySQL schema. The Laravel migrations
in ``task-mgmt-laravel`` are the single source of truth for the database
schema; Django never creates or alters these tables. Each model declares
``Meta.managed = False`` and ``Meta.db_table`` so Django's ORM reads from
and writes to the existing Laravel tables without attempting to manage
them via migrations.

Foreign-key columns (``project_id``, ``assignee_id``) are modelled as plain
``BigIntegerField`` rather than ``ForeignKey`` so Django does not try to
enforce referential integrity or generate constraint migrations - the
Laravel side already enforces the FKs at the database level.
"""

from django.db import models


class User(models.Model):
    """Mirror of the Laravel-owned ``users`` table.

    Django never writes the ``password`` column; it is included so a row
    can be loaded and re-saved without losing the hash. Authentication
    against the Sanctum-issued token table is handled separately in
    ``overdue.auth``.
    """

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, unique=True)
    password = models.CharField(max_length=255)
    role = models.CharField(max_length=16)  # 'admin' | 'user'
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'users'


class Task(models.Model):
    """Mirror of the Laravel-owned ``tasks`` table.

    The Django service is the only writer for ``status='OVERDUE'`` and the
    ``overdue_transitioned_at`` column. All other columns are written by
    Laravel; Django reads them as-is.
    """

    STATUS_CHOICES = [
        ('TODO', 'TODO'),
        ('IN_PROGRESS', 'IN_PROGRESS'),
        ('DONE', 'DONE'),
        ('OVERDUE', 'OVERDUE'),
    ]
    PRIORITY_CHOICES = [
        ('LOW', 'LOW'),
        ('MEDIUM', 'MEDIUM'),
        ('HIGH', 'HIGH'),
    ]

    id = models.BigAutoField(primary_key=True)
    project_id = models.BigIntegerField()
    assignee_id = models.BigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='TODO'
    )
    priority = models.CharField(
        max_length=8, choices=PRIORITY_CHOICES, default='MEDIUM'
    )
    due_date = models.DateField()
    overdue_transitioned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'tasks'
