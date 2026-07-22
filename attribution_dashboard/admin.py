from django.contrib import admin
from .models import Project, Lead, LeadStageHistory, AdCampaign, Booking, DataImport, CampaignSummary


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'location', 'avg_price')
    search_fields = ('name', 'location')


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'source', 'current_stage', 'tag', 'lead_status', 'created_date')
    list_filter = ('project', 'source', 'current_stage', 'tag')
    search_fields = ('name', 'campaign_name')
    date_hierarchy = 'created_date'


@admin.register(LeadStageHistory)
class LeadStageHistoryAdmin(admin.ModelAdmin):
    list_display = ('lead', 'stage', 'date_entered')
    list_filter = ('stage',)
    date_hierarchy = 'date_entered'


@admin.register(AdCampaign)
class AdCampaignAdmin(admin.ModelAdmin):
    list_display = ('campaign_name', 'platform', 'project', 'date', 'impressions', 'clicks', 'spend')
    list_filter = ('platform', 'project')
    search_fields = ('campaign_name',)
    date_hierarchy = 'date'


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('lead', 'revenue_amount', 'gtv_amount', 'booking_date')
    date_hierarchy = 'booking_date'


@admin.register(CampaignSummary)
class CampaignSummaryAdmin(admin.ModelAdmin):
    list_display = ('campaign_name', 'platform', 'project', 'period_start', 'period_end', 'spend', 'leads', 'potential', 'hot_super_hot', 'site_visits')
    list_filter = ('platform', 'project')
    search_fields = ('campaign_name',)
    date_hierarchy = 'period_end'


@admin.register(DataImport)
class DataImportAdmin(admin.ModelAdmin):
    list_display = ('import_type', 'file_name', 'project', 'platform', 'rows_created', 'rows_skipped', 'rows_failed', 'created_at')
    list_filter = ('import_type', 'project')
    readonly_fields = ('created_at',)
