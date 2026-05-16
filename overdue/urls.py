"""URL routes for the overdue app."""

from django.urls import path

from overdue.views import CloseOverdueView, HealthView, SweepView

app_name = 'overdue'

urlpatterns = [
    path('sweep', SweepView.as_view(), name='sweep'),
    path('<int:task_id>/close', CloseOverdueView.as_view(), name='close'),
    path('health', HealthView.as_view(), name='health'),
]
