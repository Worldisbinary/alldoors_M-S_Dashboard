import csv
from collections import defaultdict
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.db.models import Sum, Count, Avg
import datetime
from . import csv_import
from .forms import LeadImportForm, AdSpendImportForm, BookingImportForm, CampaignSummaryImportForm
from .models import Project, Lead, LeadStageHistory, AdCampaign, Booking, DataImport, CampaignSummary

QUALIFIED_STAGES = ['Site Visited', 'EOI Collected', 'Booking Confirmed']
CHANNELS = ['Google', 'Meta', 'LinkedIn', 'WhatsApp/SMS', 'Alldoors Website', 'Data Calling Team']
ORGANIC_CHANNELS = ['WhatsApp/SMS', 'Alldoors Website', 'Data Calling Team']

DEFAULT_COMMISSION_RATE = 0.0226  # matches seed_dummy_data.py's assumed commission rate


def safe_div(n, d):
    return n / d if d else 0.0


def bayesian_rate(successes, total, prior_mean, k=20):
    """Beta-Binomial empirical-Bayes shrinkage: pulls a small-sample rate
    toward `prior_mean` (weighted by `k` "phantom" observations) so
    low-volume channels/projects/tiers don't read as artificially
    great or terrible."""
    return (successes + k * prior_mean) / (total + k)

# (key, label, days-back). 'all' covers the full 90-day seeded window.
RANGE_PRESETS = [
    ('7d',  'Last 7 Days',   7),
    ('14d', 'Last 14 Days',  14),
    ('1m',  'Last Month',    30),
    ('6m',  'Last 6 Months', 180),
    ('1y',  'Last Year',     365),
    ('all', 'All Time',      None),
]
RANGE_DAYS = {key: days for key, _, days in RANGE_PRESETS}

def dashboard_view(request):
    end_date  = datetime.date(2026, 7, 17)
    data_start = end_date - datetime.timedelta(days=90)

    # ── 0. Project + Timeline filters ───────────────────────────────────────
    projects = Project.objects.all()
    selected_project = projects.filter(pk=request.GET.get('project')).first()

    selected_range = request.GET.get('range', 'all')
    if selected_range not in RANGE_DAYS:
        selected_range = 'all'
    range_days = RANGE_DAYS[selected_range]
    start_date = data_start if range_days is None else max(data_start, end_date - datetime.timedelta(days=range_days - 1))
    selected_range_label = next(label for key, label, _ in RANGE_PRESETS if key == selected_range)

    leads_qs         = Lead.objects.filter(created_date__range=(start_date, end_date))
    campaigns_qs      = AdCampaign.objects.filter(date__range=(start_date, end_date))
    bookings_qs       = Booking.objects.filter(booking_date__range=(start_date, end_date))
    stage_history_qs  = LeadStageHistory.objects.filter(date_entered__range=(start_date, end_date))

    if selected_project:
        leads_qs         = leads_qs.filter(project=selected_project)
        campaigns_qs      = campaigns_qs.filter(project=selected_project)
        bookings_qs       = bookings_qs.filter(lead__project=selected_project)
        stage_history_qs  = stage_history_qs.filter(lead__project=selected_project)

    # ── 1. Volume base numbers (feed the funnels below) ─────────────────────
    total_leads    = leads_qs.count()
    bookings_count = bookings_qs.count()

    # ── 1b. Cross-project economic baselines — always computed across every
    # project (ignores selected_project, like Project Performance below) so
    # per-project/per-tier Bayesian shrinkage always has a stable prior to
    # pull small samples toward, even when a single project is selected. ────
    g_leads_qs        = Lead.objects.filter(created_date__range=(start_date, end_date))
    g_bookings_qs     = Booking.objects.filter(booking_date__range=(start_date, end_date))
    g_stage_hist_qs   = LeadStageHistory.objects.filter(date_entered__range=(start_date, end_date))
    g_leads_generated = g_leads_qs.count()
    g_potential_plus  = g_leads_qs.filter(tag__in=['Potential', 'Hot', 'Super Hot']).count()
    g_hot_plus        = g_leads_qs.filter(tag__in=['Hot', 'Super Hot']).count()
    g_super_hot       = g_leads_qs.filter(tag='Super Hot').count()
    g_bookings        = g_bookings_qs.count()
    g_qualified       = g_stage_hist_qs.filter(stage__in=QUALIFIED_STAGES).values('lead').distinct().count()

    # Tier -> eventual-booking rate, global. Each numerator (g_bookings) is a
    # true subset of its denominator's tag population (Booking Confirmed
    # leads are always tagged Super Hot), except Cold — which is treated as
    # lost pipeline (0 expected value) rather than given a fabricated rate.
    g_p_notag              = safe_div(g_bookings, g_leads_generated)
    g_p_potential           = safe_div(g_bookings, g_potential_plus)
    g_p_hot                 = safe_div(g_bookings, g_hot_plus)
    g_p_super_hot            = safe_div(g_bookings, g_super_hot)
    g_avg_revenue            = float(g_bookings_qs.aggregate(avg=Avg('revenue_amount'))['avg'] or 0)
    global_quality_rate      = safe_div(g_qualified, g_leads_generated)

    # ── 2. Sales Funnel — Marketing → Sales → Tag progression ───────────────
    # Leads arrive from Marketing, Sales calls them (RNR vs Picked). Picked splits
    # into Cold (not interested) or Potential (details shared) -> Hot (site visit)
    # -> Super Hot (EOI/booking) -> Booking Confirmed.
    leads_generated = total_leads
    contacted       = leads_qs.exclude(tag='No Tag').count()
    cold_count      = leads_qs.filter(tag='Cold').count()
    potential_plus  = leads_qs.filter(tag__in=['Potential', 'Hot', 'Super Hot']).count()
    hot_plus        = leads_qs.filter(tag__in=['Hot', 'Super Hot']).count()
    super_hot       = leads_qs.filter(tag='Super Hot').count()

    def conv_pct(n):
        return round(n / leads_generated * 100, 1) if leads_generated > 0 else 0.0

    # Lead Status breakdown per Tag, for the Sankey-style fan-out under each
    # funnel column — "these leads are in these statuses".
    tag_status_groups = defaultdict(list)
    for status, tag_name in Lead.STATUS_TAG_MAP.items():
        tag_status_groups[tag_name].append(status)

    def status_breakdown(tag_name):
        rows = [
            {'name': status, 'count': leads_qs.filter(tag=tag_name, lead_status=status).count()}
            for status in tag_status_groups.get(tag_name, [])
        ]
        rows = [r for r in rows if r['count'] > 0]
        return sorted(rows, key=lambda r: -r['count'])

    # Each column carries both the Tag and the matching CRM Lead Stage(s), so the
    # single funnel below shows Marketing -> Leads -> Sales Call -> Tag -> Stage
    # exactly as described: RNR (not picked) -> No Tag / follow-up; Picked + not
    # interested -> Cold; Picked + interested -> Potential (details shared) ->
    # Hot (site visit) -> Super Hot (EOI/booking) -> Booking Confirmed.
    tag_funnel_data = [
        {
            'stage': 'Leads Generated', 'stage_note': 'Not Yet Connected',
            'count': leads_generated,
            'lost_count': leads_generated - contacted, 'lost_label': 'Not Contacted — RNR / Follow-up',
            'status_breakdown': status_breakdown('No Tag'),
            'conv_original_pct': conv_pct(leads_generated),
        },
        {
            'stage': 'Contacted (Picked Up)', 'stage_note': 'Sales Call Outcome',
            'count': contacted,
            'lost_count': cold_count, 'lost_label': 'Cold — Not Interested',
            'status_breakdown': status_breakdown('Cold'),
            'conv_original_pct': conv_pct(contacted),
        },
        {
            'stage': 'Potential', 'stage_note': 'Lead Registered / Initial Contacted',
            'count': potential_plus,
            'lost_count': potential_plus - hot_plus, 'lost_label': 'Stalled — No Site Visit',
            'status_breakdown': status_breakdown('Potential'),
            'conv_original_pct': conv_pct(potential_plus),
        },
        {
            'stage': 'Hot', 'stage_note': 'Site Visited',
            'count': hot_plus,
            'lost_count': hot_plus - super_hot, 'lost_label': 'Stalled — No EOI/Booking',
            'status_breakdown': status_breakdown('Hot'),
            'conv_original_pct': conv_pct(hot_plus),
        },
        {
            'stage': 'Super Hot', 'stage_note': 'EOI Collected',
            'count': super_hot,
            'lost_count': super_hot - bookings_count, 'lost_label': 'Pending — EOI Not Booked',
            'status_breakdown': status_breakdown('Super Hot'),
            'conv_original_pct': conv_pct(super_hot),
        },
        {
            'stage': 'Booking Confirmed', 'stage_note': 'Booking Confirmed',
            'count': bookings_count,
            'lost_count': 0, 'lost_label': '', 'status_breakdown': [],
            'conv_original_pct': conv_pct(bookings_count),
        },
    ]

    # Sales funnel — once contacted, how far did they progress (own % base).
    sales_funnel_data = []
    for s in tag_funnel_data[1:]:
        s = dict(s)
        s['conv_original_pct'] = round(s['count'] / contacted * 100, 1) if contacted > 0 else 0.0
        sales_funnel_data.append(s)

    funnel_sources = [
        {'name': ch, 'count': leads_qs.filter(source=ch).count()}
        for ch in CHANNELS
    ]

    # Sankey-style "Tag -> Lead Status" flow: for each tag tier, the specific
    # statuses its leads currently carry — "these leads are in these statuses".
    # Split the same way as the funnels: No Tag explains the not-contacted
    # pool (Marketing), Cold/Potential/Hot/Super Hot explain contacted leads (Sales).
    TAG_FLOW_COLORS = {
        'No Tag': '#94A3B8', 'Cold': '#38BDF8', 'Potential': '#818CF8',
        'Hot': '#FB923C', 'Super Hot': '#F43F5E',
    }
    def build_status_flow(tag_names):
        flow = []
        for tag_name in tag_names:
            statuses = status_breakdown(tag_name)
            total = sum(s['count'] for s in statuses)
            if total > 0:
                flow.append({
                    'tag': tag_name, 'total': total,
                    'color': TAG_FLOW_COLORS[tag_name], 'statuses': statuses,
                })
        return flow

    sales_status_flow = build_status_flow(['Cold', 'Potential', 'Hot', 'Super Hot'])

    # ── 2b. Economic Summary — Expected Pipeline Value & Blended ROAS for the
    # current filter scope. Leads still open (not booked, not Cold — Cold is
    # treated as lost pipeline) are valued at a Bayesian-shrunk probability of
    # eventually booking (shrunk toward the cross-project tier rate above)
    # times the average revenue per booking in scope. ───────────────────────
    open_no_tag    = leads_generated - contacted
    open_potential = potential_plus - hot_plus
    open_hot       = hot_plus - super_hot
    open_super_hot = super_hot - bookings_count

    p_notag_f     = bayesian_rate(bookings_count, leads_generated, g_p_notag, k=10)
    p_potential_f = bayesian_rate(bookings_count, potential_plus, g_p_potential, k=10)
    p_hot_f       = bayesian_rate(bookings_count, hot_plus, g_p_hot, k=10)
    p_super_hot_f = bayesian_rate(bookings_count, super_hot, g_p_super_hot, k=10)

    avg_revenue_scope = float(bookings_qs.aggregate(avg=Avg('revenue_amount'))['avg'] or g_avg_revenue or 0)

    expected_pipeline_value = (
        open_no_tag * p_notag_f + open_potential * p_potential_f +
        open_hot * p_hot_f + open_super_hot * p_super_hot_f
    ) * avg_revenue_scope

    total_spend_scope   = float(campaigns_qs.aggregate(t=Sum('spend'))['t'] or 0)
    total_revenue_scope = float(bookings_qs.aggregate(t=Sum('revenue_amount'))['t'] or 0)
    blended_roas = safe_div(total_revenue_scope + expected_pipeline_value, total_spend_scope)

    economic_summary = {
        'total_leads':     total_leads,
        'total_spend':     total_spend_scope,
        'total_revenue':   total_revenue_scope,
        'expected_value':  round(expected_pipeline_value, 2),
        'blended_roas':    round(blended_roas, 2),
        'bookings_count':  bookings_count,
    }

    # ── 3. Channel Performance ──────────────────────────────────────────────
    overall_qual_scope        = stage_history_qs.filter(stage__in=QUALIFIED_STAGES).values('lead').distinct().count()
    overall_quality_rate_scope = safe_div(overall_qual_scope, total_leads)
    channel_data = []
    for channel in CHANNELS:
        ch_spend    = campaigns_qs.filter(platform=channel).aggregate(total=Sum('spend'))['total'] or 0
        ch_leads    = leads_qs.filter(source=channel).count()
        ch_bookings = bookings_qs.filter(lead__source=channel).count()
        ch_revenue  = bookings_qs.filter(lead__source=channel).aggregate(total=Sum('revenue_amount'))['total'] or 0
        ch_qual     = stage_history_qs.filter(
            stage__in=QUALIFIED_STAGES, lead__source=channel
        ).values('lead').distinct().count()
        ch_eois     = stage_history_qs.filter(
            stage='EOI Collected', lead__source=channel
        ).values('lead').distinct().count()

        agg    = campaigns_qs.filter(platform=channel).aggregate(tc=Sum('clicks'), ti=Sum('impressions'))
        clicks = agg['tc'] or 0
        imps   = agg['ti'] or 0

        ctr_ch  = (clicks / imps * 100)             if imps > 0      else 0.0
        cvr_ch  = (ch_leads / clicks * 100)          if clicks > 0   else 0.0
        cpc_ch  = (float(ch_spend) / clicks)         if clicks > 0   else 0.0
        cac_ch  = (float(ch_spend) / ch_bookings)    if ch_bookings > 0 else 0.0
        cpl_ch  = (float(ch_spend) / ch_leads)       if ch_leads > 0 else 0.0
        cpql_ch = (float(ch_spend) / ch_qual)        if ch_qual > 0  else 0.0
        roas_ch = float(ch_revenue) / float(ch_spend) if ch_spend > 0 else 0.0
        qr_ch   = (ch_qual / ch_leads * 100)         if ch_leads > 0 else 0.0
        bqs_ch  = bayesian_rate(ch_qual, ch_leads, overall_quality_rate_scope, k=20) * 100

        channel_data.append({
            'platform':     channel,
            'is_organic':   channel in ORGANIC_CHANNELS,
            'spend':        float(ch_spend),
            'leads':        ch_leads,
            'bookings':     ch_bookings,
            'revenue':      float(ch_revenue),
            'eois':         ch_eois,
            'clicks':       clicks,
            'impressions':  imps,
            'ctr':          round(ctr_ch, 2),
            'cvr':          round(cvr_ch, 2),
            'cpc':          round(cpc_ch, 2),
            'cpl':          round(cpl_ch, 2),
            'cpql':         round(cpql_ch, 2),
            'cac':          round(cac_ch, 2),
            'roas':         round(roas_ch, 2),
            'quality_rate': round(qr_ch, 1),
            'bqs':          round(bqs_ch, 1),
        })

    # ── 4. Project Performance (always cross-project, but still honors the
    # timeline filter — only the Project filter itself is deliberately ignored
    # so every project stays comparable side by side) ──────────────────────
    project_data = []
    for proj in Project.objects.all():
        proj_leads_qs = Lead.objects.filter(project=proj, created_date__range=(start_date, end_date))
        proj_bookings_qs = Booking.objects.filter(lead__project=proj, booking_date__range=(start_date, end_date))

        proj_spend    = AdCampaign.objects.filter(project=proj, date__range=(start_date, end_date)).aggregate(total=Sum('spend'))['total'] or 0
        proj_leads    = proj_leads_qs.count()
        proj_bookings = proj_bookings_qs.count()
        proj_revenue  = proj_bookings_qs.aggregate(total=Sum('revenue_amount'))['total'] or 0
        proj_qual     = LeadStageHistory.objects.filter(
            stage__in=QUALIFIED_STAGES, lead__project=proj, date_entered__range=(start_date, end_date)
        ).values('lead').distinct().count()

        # Economic Intelligence — Expected Pipeline Value: leads still open in
        # this project (not booked, Cold excluded as lost pipeline), valued at
        # a Bayesian-shrunk probability of eventually booking (shrunk toward
        # the cross-project tier rate computed above) times this project's own
        # average revenue per booking (falls back to the cross-project average,
        # then to project.avg_price * a typical commission rate).
        proj_contacted      = proj_leads_qs.exclude(tag='No Tag').count()
        proj_potential_plus = proj_leads_qs.filter(tag__in=['Potential', 'Hot', 'Super Hot']).count()
        proj_hot_plus       = proj_leads_qs.filter(tag__in=['Hot', 'Super Hot']).count()
        proj_super_hot      = proj_leads_qs.filter(tag='Super Hot').count()

        proj_open_no_tag    = proj_leads - proj_contacted
        proj_open_potential = proj_potential_plus - proj_hot_plus
        proj_open_hot       = proj_hot_plus - proj_super_hot
        proj_open_super_hot = proj_super_hot - proj_bookings

        p_notag_p     = bayesian_rate(proj_bookings, proj_leads, g_p_notag, k=10)
        p_potential_p = bayesian_rate(proj_bookings, proj_potential_plus, g_p_potential, k=10)
        p_hot_p       = bayesian_rate(proj_bookings, proj_hot_plus, g_p_hot, k=10)
        p_super_hot_p = bayesian_rate(proj_bookings, proj_super_hot, g_p_super_hot, k=10)

        proj_avg_revenue = float(proj_bookings_qs.aggregate(avg=Avg('revenue_amount'))['avg'] or 0)
        if proj_avg_revenue == 0:
            proj_avg_revenue = g_avg_revenue or float(proj.avg_price) * DEFAULT_COMMISSION_RATE

        proj_expected_value = (
            proj_open_no_tag * p_notag_p + proj_open_potential * p_potential_p +
            proj_open_hot * p_hot_p + proj_open_super_hot * p_super_hot_p
        ) * proj_avg_revenue

        proj_realized_roas  = safe_div(float(proj_revenue), float(proj_spend))
        proj_projected_roas = safe_div(float(proj_revenue) + proj_expected_value, float(proj_spend))
        proj_bqs = bayesian_rate(proj_qual, proj_leads, global_quality_rate, k=20) * 100

        project_data.append({
            'id':              proj.id,
            'name':            proj.name,
            'location':        proj.location,
            'leads':           proj_leads,
            'bookings':        proj_bookings,
            'spend':           float(proj_spend),
            'revenue':         float(proj_revenue),
            'roas':            round(proj_realized_roas, 2),
            'cac':             round(float(proj_spend) / proj_bookings if proj_bookings > 0 else 0.0, 2),
            'cpl':             round(float(proj_spend) / proj_leads if proj_leads > 0 else 0.0, 2),
            'quality_rate':    round(proj_qual / proj_leads * 100 if proj_leads > 0 else 0.0, 1),
            'bqs':             round(proj_bqs, 1),
            'expected_value':  round(proj_expected_value, 2),
            'projected_roas':  round(proj_projected_roas, 2),
        })

    # ── 4b. Campaign Performance — from CampaignSummary snapshots (a separate,
    # coarser-grained import: one row per campaign per period, not tied to the
    # dashboard's own Lead/AdCampaign data or its timeline filter — it always
    # shows each campaign's most recently imported period). Honors the Project
    # filter when one is selected. "Quality"/"CPQL" here use the Potential
    # tier, matching the imported sheet's own terminology (distinct from the
    # Site-Visited-based Quality Rate used elsewhere on this dashboard). ─────
    campaign_summary_qs = CampaignSummary.objects.all()
    if selected_project:
        campaign_summary_qs = campaign_summary_qs.filter(project=selected_project)

    latest_by_campaign = {}
    for cs in campaign_summary_qs.order_by('-period_end', '-created_at'):
        key = (cs.platform, cs.campaign_name, cs.project_id)
        if key not in latest_by_campaign:
            latest_by_campaign[key] = cs

    campaign_rows = []
    for (platform, name, project_id), cs in latest_by_campaign.items():
        prev = CampaignSummary.objects.filter(
            platform=platform, campaign_name=name, project_id=project_id,
            period_end__lt=cs.period_end,
        ).order_by('-period_end').first()

        spend = float(cs.spend)
        cpl = safe_div(spend, cs.leads)
        quality_rate_pct = safe_div(cs.potential, cs.leads) * 100
        site_visit_rate_pct = safe_div(cs.site_visits, cs.leads) * 100
        high_intent_rate_pct = safe_div(cs.potential + cs.hot_super_hot, cs.leads) * 100
        cpql = safe_div(spend, cs.potential)

        marginal_utility = None
        marginal_cost = None
        if prev is not None:
            d_spend = spend - float(prev.spend)
            d_potential = cs.potential - prev.potential
            if d_spend != 0:
                marginal_utility = safe_div(d_potential, d_spend) * 1000
            if d_potential > 0 and d_spend > 0:
                marginal_cost = d_spend / d_potential

        campaign_rows.append({
            'platform': platform, 'campaign_name': name,
            'project': cs.project.name if cs.project else '—',
            'status': cs.status, 'spend': spend, 'leads': cs.leads,
            'cpl': round(cpl, 2), 'potential': cs.potential,
            'hot_super_hot': cs.hot_super_hot, 'site_visits': cs.site_visits,
            'quality_rate_pct': round(quality_rate_pct, 1),
            'site_visit_rate_pct': round(site_visit_rate_pct, 1),
            'high_intent_rate_pct': round(high_intent_rate_pct, 1),
            'cpql': round(cpql, 2),
            'marginal_utility': round(marginal_utility, 2) if marginal_utility is not None else None,
            'marginal_cost': round(marginal_cost, 2) if marginal_cost is not None else None,
            'period_start': cs.period_start, 'period_end': cs.period_end,
        })

    # Opportunity Cost — what this campaign's spend would have returned had it
    # bought quality leads at the best campaign's CPQL instead of its own.
    best_cpql = min((r['cpql'] for r in campaign_rows if r['cpql'] > 0), default=0)
    for r in campaign_rows:
        if best_cpql > 0 and r['cpql'] > best_cpql:
            r['opportunity_cost'] = round(r['spend'] - r['potential'] * best_cpql, 2)
        else:
            r['opportunity_cost'] = 0.0

    campaign_data = sorted(campaign_rows, key=lambda r: -r['spend'])

    # ── 5. Daily Timeseries ─────────────────────────────────────────────────
    date_list  = []
    curr = start_date
    while curr <= end_date:
        date_list.append(curr)
        curr += datetime.timedelta(days=1)

    chart_dates = [d.strftime('%b %d') for d in date_list]

    def mk_day_lookup(queryset_values, date_field, platform_field, value_field, default=0):
        out = {}
        for row in queryset_values:
            d = row[date_field]
            p = row[platform_field]
            out.setdefault(d, {})[p] = row[value_field] or default
        return out

    leads_by_date = {}
    for lead in leads_qs.values('created_date', 'source'):
        leads_by_date.setdefault(lead['created_date'], {}).setdefault(lead['source'], 0)
        leads_by_date[lead['created_date']][lead['source']] += 1

    spend_by_date    = mk_day_lookup(
        campaigns_qs.values('date','platform').annotate(v=Sum('spend')),
        'date','platform','v', 0.0)
    clicks_by_date   = mk_day_lookup(
        campaigns_qs.values('date','platform').annotate(v=Sum('clicks')),
        'date','platform','v', 0)
    imps_by_date     = mk_day_lookup(
        campaigns_qs.values('date','platform').annotate(v=Sum('impressions')),
        'date','platform','v', 0)
    revenue_by_date  = mk_day_lookup(
        bookings_qs.values('booking_date','lead__source').annotate(v=Sum('revenue_amount')),
        'booking_date','lead__source','v', 0.0)
    bookings_by_date = mk_day_lookup(
        bookings_qs.values('booking_date','lead__source').annotate(v=Count('id')),
        'booking_date','lead__source','v', 0)

    def build_series(lookup, channels, date_list, default=0.0):
        out = {ch: [] for ch in channels}
        out['Total'] = []
        for d in date_list:
            day = lookup.get(d, {})
            total = 0
            for ch in channels:
                v = day.get(ch, default)
                out[ch].append(float(v))
                total += float(v)
            out['Total'].append(total)
        return out

    ls = build_series(leads_by_date,    CHANNELS, date_list, 0)
    ss = build_series(spend_by_date,    CHANNELS, date_list, 0.0)
    cl = build_series(clicks_by_date,   CHANNELS, date_list, 0)
    im = build_series(imps_by_date,     CHANNELS, date_list, 0)
    rs = build_series(revenue_by_date,  CHANNELS, date_list, 0.0)
    bs = build_series(bookings_by_date, CHANNELS, date_list, 0)

    def rolling_ratio(num, den, window=7, mult=1):
        out = []
        for i in range(len(num)):
            s = max(0, i-window+1)
            n = sum(num[s:i+1])
            d = sum(den[s:i+1])
            out.append(round(n/d*mult, 2) if d > 0 else 0.0)
        return out

    all_keys = CHANNELS + ['Total']
    chart_data = {
        'channels':        CHANNELS,
        'dates':           chart_dates,
        # Raw series, keyed by channel name (+ 'Total')
        'leads':           ls,
        'spend':           ss,
        'clicks':          cl,
        'impressions':     im,
        'revenue':         rs,
        'bookings':        bs,
        # Derived rolling 7-day ratios, keyed by channel name (+ 'Total')
        'cpl':  {k: rolling_ratio(ss[k], ls[k])           for k in all_keys},
        'cac':  {k: rolling_ratio(ss[k], bs[k])           for k in all_keys},
        'ctr':  {k: rolling_ratio(cl[k], im[k], mult=100) for k in all_keys},
        'cvr':  {k: rolling_ratio(ls[k], cl[k], mult=100) for k in all_keys},
    }

    context = {
        'projects':               projects,
        'selected_project':       selected_project,
        'range_presets':          RANGE_PRESETS,
        'selected_range':         selected_range,
        'selected_range_label':   selected_range_label,
        'sales_funnel_data':      sales_funnel_data,
        'funnel_sources':         funnel_sources,
        'sales_status_flow':      sales_status_flow,
        'channel_data':           channel_data,
        'project_data':           project_data,
        'chart_data':             chart_data,
        'economic_summary':       economic_summary,
        'campaign_data':          campaign_data,
    }
    return render(request, 'attribution_dashboard/dashboard.html', context)


# ── Data Import ───────────────────────────────────────────────────────────

IMPORT_FORMS = {
    'leads':             LeadImportForm,
    'ad_spend':          AdSpendImportForm,
    'bookings':          BookingImportForm,
    'campaign_summary':  CampaignSummaryImportForm,
}


def import_view(request):
    if request.method == 'POST':
        import_type = request.POST.get('type')
        form_cls = IMPORT_FORMS.get(import_type)
        if form_cls is None:
            messages.error(request, 'Unknown import type.')
            return redirect('import_data')

        form = form_cls(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, f"Please fix the form: {form.errors.as_text()}")
            return redirect('import_data')

        upload = form.cleaned_data['file']
        project = form.cleaned_data.get('project')
        platform = form.cleaned_data.get('platform', '')

        try:
            if import_type == 'leads':
                result = csv_import.import_leads(upload, project)
            elif import_type == 'ad_spend':
                result = csv_import.import_ad_spend(upload, platform, project)
            elif import_type == 'campaign_summary':
                result = csv_import.import_campaign_summary(
                    upload, project,
                    form.cleaned_data['period_start'], form.cleaned_data['period_end'],
                    default_platform=platform,
                )
            else:
                result = csv_import.import_bookings(upload, project)
        except Exception as exc:
            messages.error(request, f"Could not read this file: {exc}")
            return redirect('import_data')

        DataImport.objects.create(
            import_type=import_type,
            file_name=upload.name,
            project=project,
            platform=platform,
            rows_created=result.created,
            rows_skipped=result.skipped,
            rows_failed=result.failed,
            error_detail='\n'.join(result.errors[:200]),
        )

        if result.created:
            messages.success(request, f"Imported {result.created} row(s). {result.skipped} skipped.")
        else:
            messages.warning(request, f"No rows imported — {result.skipped} skipped. Check the import history for reasons.")
        return redirect('import_data')

    context = {
        'lead_form':             LeadImportForm(),
        'ad_spend_form':         AdSpendImportForm(),
        'booking_form':          BookingImportForm(),
        'campaign_summary_form': CampaignSummaryImportForm(),
        'import_history':        DataImport.objects.all()[:20],
    }
    return render(request, 'attribution_dashboard/import.html', context)


def download_import_template(request, kind):
    if kind not in csv_import.TEMPLATE_ROWS:
        return HttpResponse('Unknown template', status=404)

    header, example = csv_import.TEMPLATE_ROWS[kind]
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{kind}_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerow(example)
    return response
