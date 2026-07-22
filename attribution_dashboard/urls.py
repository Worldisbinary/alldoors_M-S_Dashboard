from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('import/', views.import_view, name='import_data'),
    path('import/template/<str:kind>/', views.download_import_template, name='import_template'),
]
