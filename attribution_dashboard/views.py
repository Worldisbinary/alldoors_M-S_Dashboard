from django.shortcuts import render
from django.db.models import Sum, Count
import datetime
from .models import Project, Lead, LeadStageHistory, AdCampaign, Booking

QUALIFIED_STAGES = ['Site Visited', 'EOI Collected', 'Booking Confirmed']

def dashboard_view(request):
    end_date   = datetime.date(2026, 7, 17)
    start_date = end_date - datetime.timedelta(days=90)

    # ── 1. Global KPIs ─────────────────────────────────────────────────────
    total_leads       = Lead.objects.count()
    total_spend       = AdCampaign.objects.aggregate(total=Sum('spend'))['total'] or 0
    total_revenue     = Booking.objects.aggregate(total=Sum('revenue_amount'))['total'] or 0
    bookings_count    = Booking.objects.count()
    total_clicks      = AdCampaign.objects.aggregate(total=Sum('clicks'))['total'] or 0
    total_impressions = AdCampaign.objects.aggregate(total=Sum('impressions'))['total'] or 0

    qualified_leads = LeadStageHistory.objects.filter(
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
    closed_leads = Booking.objects.select_related('lead').all()
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

    # ── 2. Conversion Funnel ────────────────────────────────────────────────
    stages = [
        'Not Yet Connected', 'Lead Registered', 'Initial Contacted',
        'Site Visited', 'EOI Collected', 'Booking Confirmed'
    ]
    stage_counts = {
        stage: LeadStageHistory.objects.filter(stage=stage).values('lead').distinct().count()
        for stage in stages
    }

    funnel_data = []
    for i, stage in enumerate(stages):
        count      = stage_counts[stage]
        prev_count = stage_counts[stages[i-1]] if i > 0 else count
        if stage == 'Booking Confirmed':
            lost_count, drop_pct = 0, 0.0
        else:
            lost_count = Lead.objects.filter(current_stage=stage).count()
            drop_pct   = ((prev_count - count) / prev_count * 100) if prev_count > 0 else 0.0
        conv_pct = (count / total_leads * 100) if total_leads > 0 else 0.0
        funnel_data.append({
            'stage':            stage,
            'count':            count,
            'lost_count':       lost_count,
            'drop_pct':         round(drop_pct, 1),
            'conv_original_pct': round(conv_pct, 1),
        })

    # ── 3. Channel Performance ──────────────────────────────────────────────
    channels = ['Google', 'Meta', 'LinkedIn']
    channel_data = []
    for channel in channels:
        ch_spend    = AdCampaign.objects.filter(platform=channel).aggregate(total=Sum('spend'))['total'] or 0
        ch_leads    = Lead.objects.filter(source=channel).count()
        ch_bookings = Booking.objects.filter(lead__source=channel).count()
        ch_revenue  = Booking.objects.filter(lead__source=channel).aggregate(total=Sum('revenue_amount'))['total'] or 0
        ch_qual     = LeadStageHistory.objects.filter(
            stage__in=QUALIFIED_STAGES, lead__source=channel
        ).values('lead').distinct().count()
        ch_eois     = LeadStageHistory.objects.filter(
            stage='EOI Collected', lead__source=channel
        ).values('lead').distinct().count()

        agg    = AdCampaign.objects.filter(platform=channel).aggregate(tc=Sum('clicks'), ti=Sum('impressions'))
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

    # ── 4. Project Performance ──────────────────────────────────────────────
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

    leads_by_date    = {}
    for lead in Lead.objects.values('created_date', 'source'):
        leads_by_date.setdefault(lead['created_date'], {}).setdefault(lead['source'], 0)
        leads_by_date[lead['created_date']][lead['source']] += 1

    spend_by_date    = mk_day_lookup(
        AdCampaign.objects.values('date','platform').annotate(v=Sum('spend')),
        'date','platform','v', 0.0)
    clicks_by_date   = mk_day_lookup(
        AdCampaign.objects.values('date','platform').annotate(v=Sum('clicks')),
        'date','platform','v', 0)
    imps_by_date     = mk_day_lookup(
        AdCampaign.objects.values('date','platform').annotate(v=Sum('impressions')),
        'date','platform','v', 0)
    revenue_by_date  = mk_day_lookup(
        Booking.objects.values('booking_date','lead__source').annotate(v=Sum('revenue_amount')),
        'booking_date','lead__source','v', 0.0)
    bookings_by_date = mk_day_lookup(
        Booking.objects.values('booking_date','lead__source').annotate(v=Count('id')),
        'booking_date','lead__source','v', 0)

    def build_series(lookup, platforms, date_list, default=0.0):
        out = {p: [] for p in platforms}
        out['Total'] = []
        for d in date_list:
            day = lookup.get(d, {})
            total = 0
            for p in platforms:
                v = day.get(p, default)
                out[p].append(float(v))
                total += float(v)
            out['Total'].append(total)
        return out

    plats = ['Google','Meta','LinkedIn']
    ls  = build_series(leads_by_date,    plats, date_list, 0)
    ss  = build_series(spend_by_date,    plats, date_list, 0.0)
    cl  = build_series(clicks_by_date,   plats, date_list, 0)
    im  = build_series(imps_by_date,     plats, date_list, 0)
    rs  = build_series(revenue_by_date,  plats, date_list, 0.0)
    bs  = build_series(bookings_by_date, plats, date_list, 0)

    def rolling_ratio(num, den, window=7, mult=1):
        out = []
        for i in range(len(num)):
            s = max(0, i-window+1)
            n = sum(num[s:i+1])
            d = sum(den[s:i+1])
            out.append(round(n/d*mult, 2) if d > 0 else 0.0)
        return out

    chart_data = {
        'dates':           chart_dates,
        # Raw series
        'leads':           ls,
        'spend':           ss,
        'clicks':          cl,
        'impressions':     im,
        'revenue':         rs,
        'bookings':        bs,
        # Derived rolling 7-day
        'cpl_google':      rolling_ratio(ss['Google'],   ls['Google']),
        'cpl_meta':        rolling_ratio(ss['Meta'],     ls['Meta']),
        'cpl_linkedin':    rolling_ratio(ss['LinkedIn'], ls['LinkedIn']),
        'cpl_total':       rolling_ratio(ss['Total'],    ls['Total']),
        'roas_google':     rolling_ratio(rs['Google'],   ss['Google']),
        'roas_meta':       rolling_ratio(rs['Meta'],     ss['Meta']),
        'roas_linkedin':   rolling_ratio(rs['LinkedIn'], ss['LinkedIn']),
        'roas_total':      rolling_ratio(rs['Total'],    ss['Total']),
        'cac_google':      rolling_ratio(ss['Google'],   bs['Google']),
        'cac_meta':        rolling_ratio(ss['Meta'],     bs['Meta']),
        'cac_linkedin':    rolling_ratio(ss['LinkedIn'], bs['LinkedIn']),
        'cac_total':       rolling_ratio(ss['Total'],    bs['Total']),
        'ctr_google':      rolling_ratio(cl['Google'],   im['Google'],   mult=100),
        'ctr_meta':        rolling_ratio(cl['Meta'],     im['Meta'],     mult=100),
        'ctr_linkedin':    rolling_ratio(cl['LinkedIn'], im['LinkedIn'], mult=100),
        'ctr_total':       rolling_ratio(cl['Total'],    im['Total'],    mult=100),
        'cvr_google':      rolling_ratio(ls['Google'],   cl['Google'],   mult=100),
        'cvr_meta':        rolling_ratio(ls['Meta'],     cl['Meta'],     mult=100),
        'cvr_linkedin':    rolling_ratio(ls['LinkedIn'], cl['LinkedIn'], mult=100),
        'cvr_total':       rolling_ratio(ls['Total'],    cl['Total'],    mult=100),
    }

    context = {
        'kpis':         kpis,
        'funnel_data':  funnel_data,
        'channel_data': channel_data,
        'project_data': project_data,
        'chart_data':   chart_data,
    }
    return render(request, 'attribution_dashboard/dashboard.html', context)
