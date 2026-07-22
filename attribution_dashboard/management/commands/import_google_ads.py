"""Pulls campaign performance from the Google Ads API and upserts it into
AdCampaign. Requires the `google-ads` package and API credentials — see
`requirements.txt` and the README section this command was added for.

Usage:
    python manage.py import_google_ads --project "Gopalan Urban Woods" --days 7

Env vars required:
    GOOGLE_ADS_DEVELOPER_TOKEN
    GOOGLE_ADS_CLIENT_ID
    GOOGLE_ADS_CLIENT_SECRET
    GOOGLE_ADS_REFRESH_TOKEN
    GOOGLE_ADS_CUSTOMER_ID   (the ads account to pull from, digits only)
"""
import os
import datetime
from django.core.management.base import BaseCommand, CommandError
from attribution_dashboard.models import Project, AdCampaign, DataImport


class Command(BaseCommand):
    help = 'Import Google Ads campaign performance into AdCampaign via the Google Ads API.'

    def add_arguments(self, parser):
        parser.add_argument('--project', required=True, help='Project name to attribute this spend to.')
        parser.add_argument('--days', type=int, default=7, help='How many trailing days to pull (default 7).')

    def handle(self, *args, **options):
        try:
            from google.ads.googleads.client import GoogleAdsClient
        except ImportError:
            raise CommandError(
                "The 'google-ads' package isn't installed. Run: pip install google-ads"
            )

        required_env = [
            'GOOGLE_ADS_DEVELOPER_TOKEN', 'GOOGLE_ADS_CLIENT_ID', 'GOOGLE_ADS_CLIENT_SECRET',
            'GOOGLE_ADS_REFRESH_TOKEN', 'GOOGLE_ADS_CUSTOMER_ID',
        ]
        missing = [v for v in required_env if not os.environ.get(v)]
        if missing:
            raise CommandError(f"Missing required environment variables: {', '.join(missing)}")

        try:
            project = Project.objects.get(name=options['project'])
        except Project.DoesNotExist:
            raise CommandError(f"No project named '{options['project']}' found.")

        client = GoogleAdsClient.load_from_dict({
            'developer_token': os.environ['GOOGLE_ADS_DEVELOPER_TOKEN'],
            'client_id': os.environ['GOOGLE_ADS_CLIENT_ID'],
            'client_secret': os.environ['GOOGLE_ADS_CLIENT_SECRET'],
            'refresh_token': os.environ['GOOGLE_ADS_REFRESH_TOKEN'],
            'use_proto_plus': True,
        })
        customer_id = os.environ['GOOGLE_ADS_CUSTOMER_ID']
        ga_service = client.get_service('GoogleAdsService')

        end = datetime.date.today()
        start = end - datetime.timedelta(days=options['days'])
        query = f"""
            SELECT campaign.name, segments.date, metrics.impressions,
                   metrics.clicks, metrics.cost_micros
            FROM campaign
            WHERE segments.date BETWEEN '{start:%Y-%m-%d}' AND '{end:%Y-%m-%d}'
        """

        created = 0
        for row in ga_service.search_stream(customer_id=customer_id, query=query):
            for r in row.results:
                AdCampaign.objects.update_or_create(
                    platform='Google',
                    campaign_name=r.campaign.name,
                    date=r.segments.date,
                    project=project,
                    defaults={
                        'impressions': r.metrics.impressions,
                        'clicks': r.metrics.clicks,
                        'spend': r.metrics.cost_micros / 1_000_000,
                    },
                )
                created += 1

        DataImport.objects.create(
            import_type='ad_spend', file_name='Google Ads API', project=project,
            platform='Google', rows_created=created,
        )
        self.stdout.write(self.style.SUCCESS(f"Synced {created} Google Ads campaign-day rows for {project.name}."))
