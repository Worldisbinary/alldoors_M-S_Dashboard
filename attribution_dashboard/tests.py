from django.test import TestCase, Client
from django.urls import reverse
from django.core.management import call_command
from attribution_dashboard.models import Lead, LeadStageHistory, AdCampaign, Booking

class AttributionDashboardTests(TestCase):
    def setUp(self):
        # Run seeding command to generate dummy data for the tests
        call_command('seed_dummy_data')
        self.client = Client()

    def test_data_seeding(self):
        """Verify that seed_dummy_data populated the database correctly."""
        self.assertTrue(Lead.objects.count() > 0, "No leads were seeded")
        self.assertTrue(AdCampaign.objects.count() > 0, "No campaigns were seeded")
        self.assertTrue(Booking.objects.count() > 0, "No bookings were seeded")
        self.assertTrue(LeadStageHistory.objects.count() > 0, "No stage histories were seeded")

        # Test stages order
        # Make sure that every lead with Booking Confirmed has all 6 stages in LeadStageHistory
        confirmed_leads = Lead.objects.filter(current_stage='Booking Confirmed')
        for lead in confirmed_leads[:5]: # check a sample
            histories = LeadStageHistory.objects.filter(lead=lead)
            self.assertEqual(histories.count(), 6, f"Lead {lead} has {histories.count()} history stages instead of 6")

    def test_dashboard_view_resolves_and_returns_data(self):
        """Verify that the dashboard view renders and contains necessary context keys."""
        url = reverse('dashboard')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'attribution_dashboard/dashboard.html')
        
        # Verify context data structure
        self.assertIn('kpis', response.context)
        self.assertIn('funnel_data', response.context)
        self.assertIn('channel_data', response.context)
        self.assertIn('chart_data', response.context)

        # Check KPI values
        kpis = response.context['kpis']
        self.assertEqual(kpis['total_leads'], Lead.objects.count())
        self.assertEqual(kpis['bookings_count'], Booking.objects.count())
        self.assertGreater(kpis['total_spend'], 0)
        self.assertGreater(kpis['total_revenue'], 0)
        self.assertGreater(kpis['roas'], 0)

        # Check funnel counts decreasing
        funnel = response.context['funnel_data']
        self.assertEqual(len(funnel), 6)
        
        # Check cumulative counts: Lead Registered (idx 1) should be <= Not Yet Connected (idx 0)
        self.assertLessEqual(funnel[1]['count'], funnel[0]['count'])
        self.assertLessEqual(funnel[2]['count'], funnel[1]['count'])
        self.assertLessEqual(funnel[3]['count'], funnel[2]['count'])
        self.assertLessEqual(funnel[4]['count'], funnel[3]['count'])
        self.assertLessEqual(funnel[5]['count'], funnel[4]['count'])
