"""Pulls campaign performance from the Meta Marketing API and upserts it into
AdCampaign. Requires the `facebook-business` package and API credentials —
see `requirements.txt` and the README section this command was added for.

Usage:
    python manage.py import_meta_ads --project "Gopalan Urban Woods" --days 7

Env vars required:
    META_ACCESS_TOKEN   (a system-user token with ads_read permission)
    META_AD_ACCOUNT_ID  (e.g. act_1234567890)
"""
import os
import datetime
from django.core.management.base import BaseCommand, CommandError
from attribution_dashboard.models import Project, AdCampaign, DataImport


class Command(BaseCommand):
    help = 'Import Meta (Facebook/Instagram) Ads campaign performance into AdCampaign via the Marketing API.'

    def add_arguments(self, parser):
        parser.add_argument('--project', required=True, help='Project name to attribute this spend to.')
        parser.add_argument('--days', type=int, default=7, help='How many trailing days to pull (default 7).')

    def handle(self, *args, **options):
        try:
            from facebook_business.api import FacebookAdsApi
            from facebook_business.adobjects.adaccount import AdAccount
        except ImportError:
            raise CommandError(
                "The 'facebook-business' package isn't installed. Run: pip install facebook-business"
            )

        required_env = ['META_ACCESS_TOKEN', 'META_AD_ACCOUNT_ID']
        missing = [v for v in required_env if not os.environ.get(v)]
        if missing:
            raise CommandError(f"Missing required environment variables: {', '.join(missing)}")

        try:
            project = Project.objects.get(name=options['project'])
        except Project.DoesNotExist:
            raise CommandError(f"No project named '{options['project']}' found.")

        FacebookAdsApi.init(access_token=os.environ['META_ACCESS_TOKEN'])
        account = AdAccount(os.environ['META_AD_ACCOUNT_ID'])

        end = datetime.date.today()
        start = end - datetime.timedelta(days=options['days'])
        insights = account.get_insights(params={
            'time_range': {'since': str(start), 'until': str(end)},
            'time_increment': 1,
            'level': 'campaign',
            'fields': ['campaign_name', 'date_start', 'impressions', 'inline_link_clicks', 'spend'],
        })

        created = 0
        for row in insights:
            AdCampaign.objects.update_or_create(
                platform='Meta',
                campaign_name=row.get('campaign_name'),
                date=row.get('date_start'),
                project=project,
                defaults={
                    'impressions': int(row.get('impressions', 0)),
                    'clicks': int(row.get('inline_link_clicks', 0)),
                    'spend': float(row.get('spend', 0)),
                },
            )
            created += 1

        DataImport.objects.create(
            import_type='ad_spend', file_name='Meta Ads API', project=project,
            platform='Meta', rows_created=created,
        )
        self.stdout.write(self.style.SUCCESS(f"Synced {created} Meta Ads campaign-day rows for {project.name}."))
