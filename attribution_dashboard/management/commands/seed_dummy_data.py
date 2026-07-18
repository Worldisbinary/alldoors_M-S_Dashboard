import random
import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from attribution_dashboard.models import Project, Lead, LeadStageHistory, AdCampaign, Booking

class Command(BaseCommand):
    help = 'Seeds the database with realistic marketing attribution dummy data in Indian Rupees (Rs.) matching the CRM.'

    def handle(self, *args, **options):
        self.stdout.write('Clearing existing attribution and project data...')
        LeadStageHistory.objects.all().delete()
        Booking.objects.all().delete()
        Lead.objects.all().delete()
        AdCampaign.objects.all().delete()
        Project.objects.all().delete()
        self.stdout.write('Existing data cleared.')

        # 1. Create Projects matching the CRM properties
        projects_data = [
            {'name': 'Meridian Park At The Prestige City', 'location': 'Sarjapura', 'avg_price': 14000000},   # ₹1.4 Crore
            {'name': 'Gopalan Urban Woods', 'location': 'Brookfield', 'avg_price': 28000000},              # ₹2.8 Crore
            {'name': 'Snn Raj Greenbay', 'location': 'Electronic City', 'avg_price': 16000000},            # ₹1.6 Crore
            {'name': 'Brigade El Dorado', 'location': 'Bagaluru', 'avg_price': 9000000}                    # ₹90 Lakh
        ]
        
        projects = []
        for p_info in projects_data:
            proj = Project.objects.create(
                name=p_info['name'],
                location=p_info['location'],
                avg_price=p_info['avg_price']
            )
            projects.append(proj)
        
        self.stdout.write(f"Seeded {len(projects)} projects.")

        # Lists for generating realistic lead names
        first_names = [
            'Jitendra', 'Madhusudhan', 'Kamal', 'Jayesh', 'Aarav', 'Vihaan', 'Aditya', 'Sai',
            'Arjun', 'Rohan', 'Amit', 'Sanjay', 'Rahul', 'Pooja', 'Neha', 'Priyanka', 'Anjali',
            'Sunita', 'Geeta', 'Karan', 'Vikram', 'Rajesh', 'Anil', 'Deepak', 'Vijay', 'Sunil',
            'Manish', 'Harish', 'Suresh', 'Ramesh', 'Alok', 'Abhishek', 'Pradeep', 'Sandeep',
            'Ritu', 'Kiran', 'Jyoti', 'Shalini', 'Renu', 'Preeti'
        ]
        last_names = [
            'Chauhan', 'Reddy', 'Sharma', 'Patel', 'Kumar', 'Singh', 'Gupta', 'Mehra',
            'Joshi', 'Verma', 'Nair', 'Pillai', 'Rao', 'Iyer', 'Sinha', 'Kapoor', 'Malhotra',
            'Saxena', 'Mishra', 'Pandey', 'Bose', 'Chatterjee', 'Mukherjee', 'Sen', 'Das',
            'Roy', 'Choudhury', 'Jadhav', 'Deshmukh', 'Kulkarni', 'Naidu', 'Shetty', 'Hegde'
        ]

        # Define platform properties and campaign targets
        # Google: high cost, high volume, low CTR, average ROAS
        # Meta: medium cost, medium volume, high CTR, good ROAS
        # LinkedIn: very high CPC, low volume, premium leads, high ROAS (high ticket projects)
        campaigns_config = {
            'Google': [
                # Google: decent volume, lower CVR — quality but not quantity
                {'name': 'Google_Search_Brand',   'cvr': 0.022, 'ctr_range': (0.025, 0.045), 'cpc_range': (80, 120),  'imp_range': (2000, 3000)},
                {'name': 'Google_Search_Generic', 'cvr': 0.016, 'ctr_range': (0.015, 0.025), 'cpc_range': (110, 150), 'imp_range': (3500, 5000)},
            ],
            'Meta': [
                # Meta: high CTR, high CVR, high volume — social discovery funnel
                {'name': 'Meta_Prospecting_Lookalike', 'cvr': 0.042, 'ctr_range': (0.045, 0.065), 'cpc_range': (45, 65), 'imp_range': (3000, 5000)},
                {'name': 'Meta_Retargeting_Catalog',   'cvr': 0.065, 'ctr_range': (0.060, 0.090), 'cpc_range': (65, 85), 'imp_range': (1500, 2800)},
            ],
            'LinkedIn': [
                # LinkedIn: premium, low volume, high CPC — targets high-ticket projects
                {'name': 'LinkedIn_LeadGen_Forms', 'cvr': 0.065, 'ctr_range': (0.012, 0.025), 'cpc_range': (350, 480), 'imp_range': (400, 800)},
            ]
        }

        # Organic / owned channels — no ad spend, leads come in directly (WhatsApp/SMS
        # broadcasts, direct website enquiries, outbound calling on the existing database).
        organic_channels = {
            'WhatsApp/SMS':        {'leads_per_day_range': (2, 6)},
            'Alldoors Website':    {'leads_per_day_range': (3, 8)},
            'Data Calling Team':   {'leads_per_day_range': (4, 10)},
        }

        # Funnel stages
        stages = [
            'Not Yet Connected',
            'Lead Registered',
            'Initial Contacted',
            'Site Visited',
            'EOI Collected',
            'Booking Confirmed'
        ]

        def derive_call_and_tag(stage):
            """Maps a CRM stage to a (call_status, tag) pair reflecting the real
            Sales workflow: RNR (not picked) / Picked -> Cold (not interested) or
            Potential (details shared) -> Hot (site visit) -> Super Hot (EOI/booking)."""
            if stage == 'Not Yet Connected':
                r = random.random()
                if r < 0.55:
                    return '', 'No Tag'            # not attempted yet
                elif r < 0.80:
                    return 'RNR', 'No Tag'          # attempted, not picked up
                else:
                    return 'Picked', 'Cold'         # picked up, not interested
            elif stage in ('Lead Registered', 'Initial Contacted'):
                return 'Picked', 'Potential'
            elif stage == 'Site Visited':
                return 'Picked', 'Hot'
            else:  # EOI Collected, Booking Confirmed
                return 'Picked', 'Super Hot'

        # Stop weights (corresponds to CRM's conversion ratios):
        # Not Yet Connected: ~40% drop-off
        # Lead Registered: ~15% drop-off
        # Initial Contacted: ~25% drop-off
        # Site Visited: ~10% drop-off
        # EOI Collected: ~6% drop-off
        # Booking Confirmed: ~4% conversion
        stage_stop_weights = [0.40, 0.15, 0.25, 0.15, 0.0445, 0.0055]

        # 90 days date range
        end_date = datetime.date(2026, 7, 17)
        start_date = end_date - datetime.timedelta(days=90)

        total_leads_created = 0
        total_bookings_created = 0
        total_spend_created = 0
        total_gtv_created = 0
        total_revenue_created = 0

        current_date = start_date

        self.stdout.write(f"Generating data from {start_date} to {end_date}...")

        def create_lead(source, campaign_name, created_date, proj):
            nonlocal total_leads_created, total_bookings_created, total_gtv_created, total_revenue_created
            name = f"{random.choice(first_names)} {random.choice(last_names)}"

            current_stage = random.choices(stages, weights=stage_stop_weights, k=1)[0]
            final_stage_idx = stages.index(current_stage)
            call_status, tag = derive_call_and_tag(current_stage)

            lead = Lead.objects.create(
                name=name,
                source=source,
                campaign_name=campaign_name,
                created_date=created_date,
                current_stage=current_stage,
                call_status=call_status,
                tag=tag,
                project=proj
            )
            total_leads_created += 1

            lead_history_date = created_date
            for s_idx in range(final_stage_idx + 1):
                if s_idx > 0:
                    delay = random.randint(0, 3)
                    lead_history_date = min(lead_history_date + datetime.timedelta(days=delay), end_date)

                LeadStageHistory.objects.create(
                    lead=lead,
                    stage=stages[s_idx],
                    date_entered=lead_history_date
                )

            if current_stage == 'Booking Confirmed':
                gtv = proj.avg_price * random.uniform(0.95, 1.05)
                commission_rate = random.uniform(0.0224, 0.0228)
                rev = gtv * commission_rate

                Booking.objects.create(
                    lead=lead,
                    revenue_amount=rev,
                    gtv_amount=gtv,
                    booking_date=lead_history_date
                )
                total_bookings_created += 1
                total_gtv_created += gtv
                total_revenue_created += rev

        with transaction.atomic():
            while current_date <= end_date:
                for platform, campaigns in campaigns_config.items():
                    for cp in campaigns:
                        # Distribute campaign spend across projects
                        # LinkedIn is premium, targets Gopalan Urban Woods and Prestige Meridian Park mainly
                        if platform == 'LinkedIn':
                            target_projects = [p for p in projects if p.name in ['Gopalan Urban Woods', 'Meridian Park At The Prestige City']]
                        else:
                            target_projects = projects

                        for proj in target_projects:
                            # Generate Ad Campaign Metrics
                            # Scale down LinkedIn volume and scale up spend to match LinkedIn premium costs
                            scale_factor = 0.5 if platform == 'LinkedIn' else 1.0
                            
                            imp = int(random.randint(*cp['imp_range']) * scale_factor)
                            ctr = random.uniform(*cp['ctr_range'])
                            clicks = int(imp * ctr)
                            cpc = random.uniform(*cp['cpc_range'])
                            spend = clicks * cpc
                            
                            total_spend_created += spend

                            AdCampaign.objects.create(
                                platform=platform,
                                campaign_name=cp['name'],
                                date=current_date,
                                impressions=imp,
                                clicks=clicks,
                                spend=spend,
                                project=proj
                            )

                            # Generate Leads
                            num_leads = int(clicks * cp['cvr'])
                            
                            # Scale lead counts to ensure we hit total leads count ~25k?
                            # Wait, in the screenshots, the CRM shows All Leads: 25178, Closures: 137.
                            # So conversion rate is 137 / 25178 = 0.54% overall.
                            # To get exactly 137 closures with 0.54% overall conversion rate, we need ~25,000 total leads!
                            # Let's adjust the scale to generate ~280 leads/day so that over 90 days we get ~25,000 leads and ~135 closures!
                            # This is perfect!
                            
                            # Scale up leads generation to match All Leads = 25178
                            leads_multiplier = 5.5 # multiply lead numbers to scale up
                            num_leads = int(num_leads * leads_multiplier)
                            num_leads = max(0, num_leads + random.randint(-4, 4))

                            for _ in range(num_leads):
                                create_lead(platform, cp['name'], current_date, proj)

                # Organic / owned channels — no ad spend, leads generated directly per project
                for source, cfg in organic_channels.items():
                    for proj in projects:
                        num_leads = random.randint(*cfg['leads_per_day_range'])
                        for _ in range(num_leads):
                            create_lead(source, source, current_date, proj)

                current_date += datetime.timedelta(days=1)

        # Scale final display printout to Crore / Lakhs to confirm
        spend_cr = total_spend_created / 10000000
        gtv_cr = total_gtv_created / 10000000
        rev_cr = total_revenue_created / 10000000

        self.stdout.write(self.style.SUCCESS(
            f"Successfully seeded database with:\n"
            f" - {total_leads_created} Total Leads (All)\n"
            f" - {total_bookings_created} Closures (Bookings)\n"
            f" - Total Spend: Rs. {total_spend_created:,.2f} ({spend_cr:.2f} Cr)\n"
            f" - Total GTV: Rs. {total_gtv_created:,.2f} ({gtv_cr:.2f} Cr)\n"
            f" - Total Revenue: Rs. {total_revenue_created:,.2f} ({rev_cr:.2f} Cr)\n"
            f" - Overall ROAS: {total_revenue_created/total_spend_created:.2f}x"
        ))
