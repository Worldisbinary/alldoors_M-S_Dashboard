from django.shortcuts import render
from django.db.models import Sum, Count
import datetime
from .models import Project, Lead, LeadStageHistory, AdCampaign, Booking

QUALIFIED_STAGES = ['Site Visited', 'EOI Collected', 'Booking Confirmed']
CHANNELS = ['Google', 'Meta', 'LinkedIn', 'WhatsApp/SMS', 'Alldoors Website', 'Data Calling Team']
ORGANIC_CHANNELS = ['WhatsApp/SMS', 'Alldoors Website', 'Data Calling Team']

def dashboard_view(request):
    end_date   = datetime.date(2026, 7, 17)
    start_date = end_date - datetime.timedelta(days=90)

    # ── 0. Project filter ───────────────────────────────────────────────────
    projects = Project.objects.all()
    selected_project = projects.filter(pk=request.GET.get('project')).first()

    leads_qs         = Lead.objects.all()
    campaigns_qs      = AdCampaign.objects.all()
    bookings_qs       = Booking.objects.all()
    stage_history_qs  = LeadStageHistory.objects.all()

    if selected_project:
        leads_qs         = leads_qs.filter(project=selected_project)
        campaigns_qs      = campaigns_qs.filter(project=selected_project)
        bookings_qs       = bookings_qs.filter(lead__project=selected_project)
        stage_history_qs  = stage_history_qs.filter(lead__project=selected_project)

    # ── 1. Global KPIs ─────────────────────────────────────────────────────
    total_leads       = leads_qs.count()
    total_spend       = campaigns_qs.aggregate(total=Sum('spend'))['total'] or 0
    total_revenue     = bookings_qs.aggregate(total=Sum('revenue_amount'))['total'] or 0
    bookings_count    = bookings_qs.count()
    total_clicks      = campaigns_qs.aggregate(total=Sum('clicks'))['total'] or 0
    total_impressions = campaigns_qs.aggregate(total=Sum('impressions'))['total'] or 0

    qualified_leads = stage_history_qs.filter(
        stage__in=QUALIFIED_STAGES
    ).values('lead').distinct().count()

    # PPT formulas
    cpl          = float(total_spend) / total_leads          if total_leads > 0          else 0.0
    cpql         = float(total_spend) / qualified_leads      if qualified_leads > 0      else 0.0
    cac          = float(total_spend) / bookings_count       if bookings_count > 0       else 0.0
    roas         = float(total_revenue) / float(total_spend) if total_spend > 0          else 0.0
    quality_rate = (qualified_leads / total_leads * 100)     if total_leads > 0          else 0.0
    overall_ctr  = (float(total_clicks) / float(total_impressions) * 100) if total_impressions > 0 else 0.0
    overall_cvr  = (total_leads / float(total_clicks) * 100)              if total_clicks > 0     else 0.0

    # Time to Close
    closed_leads = bookings_qs.select_related('lead').all()
    if closed_leads.exists():
        deltas = [
            (b.booking_date - b.lead.created_date).days
            for b in closed_leads
            if b.booking_date and b.lead.created_date
        ]
        avg_time_to_close = sum(deltas) / len(deltas) if deltas else 0.0
    else:
        avg_time_to_close = 0.0

    kpis = {
        'total_leads':        total_leads,
        'total_spend':        float(total_spend),
        'total_revenue':      float(total_revenue),
        'bookings_count':     bookings_count,
        'qualified_leads':    qualified_leads,
        'total_clicks':       total_clicks,
        'total_impressions':  total_impressions,
        'cpl':                round(cpl, 2),
        'cpql':               round(cpql, 2),
        'cac':                round(cac, 2),
        'roas':               round(roas, 2),
        'quality_rate':       round(quality_rate, 1),
        'overall_ctr':        round(overall_ctr, 2),
        'overall_cvr':        round(overall_cvr, 2),
        'avg_time_to_close':  round(avg_time_to_close, 1),
    }

    # ── 2. Sales Funnel — Marketing → Sales → Tag progression ───────────────
    # Leads arrive from Marketing, Sales calls them (RNR vs Picked). Picked splits
    # into Cold (not interested) or Potential (details shared) -> Hot (site visit)
    # -> Super Hot (EOI/booking) -> Booking Confirmed.
    leads_generated = total_leads
    contacted       = leads_qs.filter(call_status='Picked').count()
    rnr_count       = leads_qs.filter(call_status='RNR').count()
    cold_count      = leads_qs.filter(tag='Cold').count()
    potential_plus  = leads_qs.filter(tag__in=['Potential', 'Hot', 'Super Hot']).count()
    hot_plus        = leads_qs.filter(tag__in=['Hot', 'Super Hot']).count()
    super_hot       = leads_qs.filter(tag='Super Hot').count()

    def conv_pct(n):
        return round(n / leads_generated * 100, 1) if leads_generated > 0 else 0.0

    tag_funnel_data = [
        {
            'stage': 'Leads Generated', 'count': leads_generated,
            'lost_count': leads_generated - contacted, 'lost_label': 'RNR / Follow-up',
            'conv_original_pct': conv_pct(leads_generated),
        },
        {
            'stage': 'Contacted (Picked Up)', 'count': contacted,
            'lost_count': cold_count, 'lost_label': 'Cold — Not Interested',
            'conv_original_pct': conv_pct(contacted),
        },
        {
            'stage': 'Potential (Details Shared)', 'count': potential_plus,
            'lost_count': potential_plus - hot_plus, 'lost_label': 'Stalled — No Site Visit',
            'conv_original_pct': conv_pct(potential_plus),
        },
        {
            'stage': 'Hot (Site Visit)', 'count': hot_plus,
            'lost_count': hot_plus - super_hot, 'lost_label': 'Stalled — No EOI/Booking',
            'conv_original_pct': conv_pct(hot_plus),
        },
        {
            'stage': 'Super Hot (EOI/Booking)', 'count': super_hot,
            'lost_count': super_hot - bookings_count, 'lost_label': 'Pending — EOI Not Booked',
            'conv_original_pct': conv_pct(super_hot),
        },
        {
            'stage': 'Booking Confirmed', 'count': bookings_count,
            'lost_count': 0, 'lost_label': '',
            'conv_original_pct': conv_pct(bookings_count),
        },
    ]

    funnel_sources = [
        {'name': ch, 'count': leads_qs.filter(source=ch).count()}
        for ch in CHANNELS
    ]

    # ── 2b. Lead Stage Funnel — raw CRM stages (Not Yet Connected → Booking) ─
    stages_list = [
        'Not Yet Connected', 'Lead Registered', 'Initial Contacted',
        'Site Visited', 'EOI Collected', 'Booking Confirmed'
    ]
    stage_counts = {
        stage: stage_history_qs.filter(stage=stage).values('lead').distinct().count()
        for stage in stages_list
    }
    stage_funnel_data = []
    for i, stage in enumerate(stages_list):
        count = stage_counts[stage]
        if stage == 'Booking Confirmed':
            lost_count = 0
        else:
            lost_count = leads_qs.filter(current_stage=stage).count()
        stage_funnel_data.append({
            'stage': stage, 'count': count,
            'lost_count': lost_count, 'lost_label': 'Stuck at This Stage',
            'conv_original_pct': conv_pct(count),
        })

    # ── 3. Channel Performance ──────────────────────────────────────────────
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
        })

    # ── 4. Project Performance (always cross-project, for comparison) ──────
    project_data = []
    for proj in Project.objects.all():
        proj_spend    = AdCampaign.objects.filter(project=proj).aggregate(total=Sum('spend'))['total'] or 0
        proj_leads    = Lead.objects.filter(project=proj).count()
        proj_bookings = Booking.objects.filter(lead__project=proj).count()
        proj_revenue  = Booking.objects.filter(lead__project=proj).aggregate(total=Sum('revenue_amount'))['total'] or 0
        proj_qual     = LeadStageHistory.objects.filter(
            stage__in=QUALIFIED_STAGES, lead__project=proj
        ).values('lead').distinct().count()

        project_data.append({
            'id':           proj.id,
            'name':         proj.name,
            'location':     proj.location,
            'leads':        proj_leads,
            'bookings':     proj_bookings,
            'spend':        float(proj_spend),
            'revenue':      float(proj_revenue),
            'roas':         round(float(proj_revenue) / float(proj_spend) if proj_spend > 0 else 0.0, 2),
            'cac':          round(float(proj_spend) / proj_bookings if proj_bookings > 0 else 0.0, 2),
            'cpl':          round(float(proj_spend) / proj_leads if proj_leads > 0 else 0.0, 2),
            'quality_rate': round(proj_qual / proj_leads * 100 if proj_leads > 0 else 0.0, 1),
        })

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
        'roas': {k: rolling_ratio(rs[k], ss[k])           for k in all_keys},
        'cac':  {k: rolling_ratio(ss[k], bs[k])           for k in all_keys},
        'ctr':  {k: rolling_ratio(cl[k], im[k], mult=100) for k in all_keys},
        'cvr':  {k: rolling_ratio(ls[k], cl[k], mult=100) for k in all_keys},
    }

    context = {
        'projects':          projects,
        'selected_project':  selected_project,
        'kpis':              kpis,
        'tag_funnel_data':   tag_funnel_data,
        'stage_funnel_data': stage_funnel_data,
        'funnel_sources':    funnel_sources,
        'channel_data':      channel_data,
        'project_data':      project_data,
        'chart_data':        chart_data,
    }
    return render(request, 'attribution_dashboard/dashboard.html', context)
