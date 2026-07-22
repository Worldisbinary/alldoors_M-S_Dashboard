import datetime
from django import forms
from .models import Project, AdCampaign, CampaignSummary

FILE_WIDGET = forms.ClearableFileInput(attrs={'accept': '.csv,.xlsx,.xlsm'})
FILE_HELP = "CSV or Excel (.xlsx)."


class LeadImportForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.all(), required=False,
        help_text="Default project for rows without a Project column.",
    )
    file = forms.FileField(widget=FILE_WIDGET, help_text=FILE_HELP)


class AdSpendImportForm(forms.Form):
    platform = forms.ChoiceField(choices=AdCampaign.PLATFORM_CHOICES)
    project = forms.ModelChoiceField(queryset=Project.objects.all())
    file = forms.FileField(widget=FILE_WIDGET, help_text=FILE_HELP)


class BookingImportForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.all(), required=False,
        help_text="Narrows lead-matching to this project (recommended if names repeat across projects).",
    )
    file = forms.FileField(widget=FILE_WIDGET, help_text=FILE_HELP)


class CampaignSummaryImportForm(forms.Form):
    period_start = forms.DateField(
        initial=lambda: datetime.date.today() - datetime.timedelta(days=29),
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    period_end = forms.DateField(
        initial=datetime.date.today,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all(), required=False,
        help_text="Fallback when a Campaign name doesn't match a project name exactly.",
    )
    platform = forms.ChoiceField(
        choices=[('', '(read from file)')] + CampaignSummary.PLATFORM_CHOICES, required=False,
        help_text="Only needed if the file has no Platform column (e.g. a single-platform tab).",
    )
    file = forms.FileField(widget=FILE_WIDGET, help_text=FILE_HELP)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('period_start'), cleaned.get('period_end')
        if start and end and start > end:
            raise forms.ValidationError("Period Start must be on or before Period End.")
        return cleaned
