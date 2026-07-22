from django.db import models

class Project(models.Model):
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255)
    avg_price = models.DecimalField(max_digits=12, decimal_places=2) # Average unit price in Rs.

    def __str__(self):
        return f"{self.name} ({self.location})"


class Lead(models.Model):
    PLATFORM_CHOICES = [
        ('Google', 'Google'),
        ('Meta', 'Meta'),
        ('LinkedIn', 'LinkedIn'),
        ('WhatsApp/SMS', 'WhatsApp/SMS'),
        ('Alldoors Website', 'Alldoors Website'),
        ('Data Calling Team', 'Data Calling Team'),
    ]

    STAGE_CHOICES = [
        ('Not Yet Connected', 'Not Yet Connected'),
        ('Lead Registered', 'Lead Registered'),
        ('Initial Contacted', 'Initial Contacted'),
        ('Site Visited', 'Site Visited'),
        ('EOI Collected', 'EOI Collected'),
        ('Booking Confirmed', 'Booking Confirmed'),
    ]

    CALL_STATUS_CHOICES = [
        ('RNR', 'RNR — Not Picked'),
        ('Picked', 'Picked Up'),
    ]

    TAG_CHOICES = [
        ('No Tag', 'No Tag'),
        ('Cold', 'Cold'),
        ('Potential', 'Potential'),
        ('Hot', 'Hot'),
        ('Super Hot', 'Super Hot'),
    ]

    # Fine-grained sales action/outcome. When set, it is authoritative for `tag`
    # (see STATUS_TAG_MAP below) — leads without a recorded status yet fall back
    # to the stage-based tag logic in the seed script / views.
    STATUS_CHOICES = [
        ('Not Connected', 'Not Connected'),
        ('Follow Up', 'Follow Up'),
        ('Follow Up RNR', 'Follow Up RNR'),
        ('Not Interested', 'Not Interested'),
        ('Junk', 'Junk'),
        ('Interested', 'Interested'),
        ('Requirement Collected', 'Requirement Collected'),
        ('Property Changed', 'Property Changed'),
        ('Visit Dropped', 'Visit Dropped'),
        ('Visit Unsuccessful', 'Visit Unsuccessful'),
        ('Closed', 'Closed'),
        ('Booking Dropped', 'Booking Dropped'),
        ('Eoi Dropped', 'Eoi Dropped'),
    ]

    # Maps each Lead Status to the Tag it implies — tag is derived from status.
    STATUS_TAG_MAP = {
        'Not Connected':         'No Tag',
        'Follow Up':             'No Tag',
        'Follow Up RNR':         'No Tag',
        'Not Interested':        'Cold',
        'Junk':                  'Cold',
        'Interested':            'Potential',
        'Requirement Collected': 'Potential',
        'Property Changed':      'Potential',
        'Visit Dropped':         'Potential',
        'Visit Unsuccessful':    'Hot',
        'Closed':                'Super Hot',
        'Booking Dropped':       'Super Hot',
        'Eoi Dropped':           'Super Hot',
    }

    name = models.CharField(max_length=255)
    source = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    campaign_name = models.CharField(max_length=255)
    created_date = models.DateField()
    current_stage = models.CharField(max_length=50, choices=STAGE_CHOICES, default='Not Yet Connected')
    call_status = models.CharField(max_length=20, choices=CALL_STATUS_CHOICES, blank=True)
    tag = models.CharField(max_length=20, choices=TAG_CHOICES, default='No Tag')
    lead_status = models.CharField(max_length=30, choices=STATUS_CHOICES, blank=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='leads')

    def __str__(self):
        return f"{self.name} ({self.source} -> {self.project.name} -> {self.current_stage})"


class LeadStageHistory(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='stage_history')
    stage = models.CharField(max_length=50, choices=Lead.STAGE_CHOICES)
    date_entered = models.DateField()

    def __str__(self):
        return f"{self.lead.name} entered {self.stage} on {self.date_entered}"


class AdCampaign(models.Model):
    PLATFORM_CHOICES = [
        ('Google', 'Google'),
        ('Meta', 'Meta'),
        ('LinkedIn', 'LinkedIn'),
    ]

    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    campaign_name = models.CharField(max_length=255)
    date = models.DateField()
    impressions = models.IntegerField()
    clicks = models.IntegerField()
    spend = models.DecimalField(max_digits=10, decimal_places=2) # Spend in Rs.
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='ad_campaigns')

    def __str__(self):
        return f"{self.campaign_name} ({self.platform} - {self.project.name}) - {self.date}: Spend Rs. {self.spend}"


class Booking(models.Model):
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='bookings')
    revenue_amount = models.DecimalField(max_digits=10, decimal_places=2) # Commission revenue in Rs.
    gtv_amount = models.DecimalField(max_digits=12, decimal_places=2) # Total property transaction value in Rs.
    booking_date = models.DateField()

    def __str__(self):
        return f"Booking for {self.lead.name} ({self.lead.project.name}) - Revenue: Rs. {self.revenue_amount}, GTV: Rs. {self.gtv_amount}"


class CampaignSummary(models.Model):
    """A period snapshot of a single campaign's performance, as reported by
    the ad platform + a lead-outcome overlay (Potential/Hot-Super Hot/Site
    Visits) — the granularity real campaign spreadsheets are usually kept
    at. Deliberately separate from Lead/AdCampaign: those model individual,
    dated leads and daily spend; this models an aggregate snapshot for a
    date range, with no per-lead detail. Kept as its own table so importing
    a spreadsheet like this never fabricates fake Lead rows."""
    PLATFORM_CHOICES = [
        ('Google', 'Google'),
        ('Meta', 'Meta'),
        ('LinkedIn', 'LinkedIn'),
    ]

    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    campaign_name = models.CharField(max_length=255)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=50, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    spend = models.DecimalField(max_digits=12, decimal_places=2)
    leads = models.IntegerField(default=0)
    potential = models.IntegerField(default=0)       # "quality lead" tier in this sheet's own terms
    hot_super_hot = models.IntegerField(default=0)
    site_visits = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period_end', '-created_at']

    def __str__(self):
        return f"{self.campaign_name} ({self.platform}) {self.period_start}..{self.period_end}"


class DataImport(models.Model):
    IMPORT_TYPES = [
        ('leads', 'Leads'),
        ('ad_spend', 'Ad Spend'),
        ('bookings', 'Bookings'),
        ('campaign_summary', 'Campaign Summary'),
    ]

    import_type = models.CharField(max_length=20, choices=IMPORT_TYPES)
    file_name = models.CharField(max_length=255)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    platform = models.CharField(max_length=50, blank=True)
    rows_created = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    rows_failed = models.IntegerField(default=0)
    error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_import_type_display()} import — {self.file_name} ({self.created_at:%Y-%m-%d %H:%M})"
