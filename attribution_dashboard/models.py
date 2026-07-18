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

    name = models.CharField(max_length=255)
    source = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    campaign_name = models.CharField(max_length=255)
    created_date = models.DateField()
    current_stage = models.CharField(max_length=50, choices=STAGE_CHOICES, default='Not Yet Connected')
    call_status = models.CharField(max_length=20, choices=CALL_STATUS_CHOICES, blank=True)
    tag = models.CharField(max_length=20, choices=TAG_CHOICES, default='No Tag')
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
