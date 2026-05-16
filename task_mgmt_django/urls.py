"""task_mgmt_django URL Configuration.

The overdue service mounts its routes under /api/overdue/ via the overdue
app's own urls.py. Concrete routes (sweep, close, health) are wired in
task 10.5.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/overdue/', include('overdue.urls')),
]
