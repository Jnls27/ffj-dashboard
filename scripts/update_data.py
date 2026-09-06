#!/usr/bin/env python3
"""
Actualización diaria del dashboard FFJ (Meta + TikTok Ads).

Qué hace:
  1. Calcula period_to = ayer (hora España).
  2. Pide a Windsor.ai (API REST pública, sin SDK) los datos agregados de
     Meta y TikTok: mensual, semanal, últimos 7d/7d previos, diario y por
     campaña, más el histórico 2025 para la comparativa.
  3. Calcula las fórmulas SIEMPRE sobre agregados (SUM/SUM), nunca como
     promedio de tasas diarias -- ver sección FORMULAS.
  4. Escribe data_meta.json y data_tt.json en la raíz del repo, listos para
     que index.html los cargue con fetch().

Requiere la variable de entorno WINDSOR_API_KEY (GitHub secret en producción).
No imprime ni loguea la API key en ningún caso.
"""

import os
import sys
import json
import time
from datetime import date, datetime, timedelta, timezone

import requests

WINDSOR_BASE = "https://connectors.windsor.ai"
API_KEY = os.environ.get("WINDSOR_API_KEY")
if not API_KEY:
    print("ERROR: falta la variable de entorno WINDSOR_API_KEY", file=sys.stderr)
    sys.exit(1)

META_ACCOUNT = "867148350599872"
TT_ACCOUNT = "7303614489256083457"
PERIOD_FROM_META = "2026-01-01"
PERIOD_FROM_TT = "2026-01-01"
PERIOD_FROM_2025_META = "2025-01-01"
PERIOD_FROM_2025_TT = "2025-01-01"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Zona horaria España (simplificado: usamos CET/CEST vía offset fijo +1/+2
# no es exacto en el cambio de hora, pero para "hasta ayer" el margen de
# error de una hora no afecta la fecha calendario en la práctica).
def madrid_yesterday():
    now_utc = datetime.now(timezone.utc)
    # Aproximación: España está en UTC+1 (invierno) o UTC+2 (verano).
    # Usamos +2 en meses de verano (abril-octubre) y +1 el resto, suficiente
    # para calcular la fecha de "ayer" sin depender de zoneinfo/tz data.
    offset = 2 if 4 <= now_utc.month <= 10 else 1
    now_madrid = now_utc + timedelta(hours=offset)
    yesterday = (now_madrid - timedelta(days=1)).date()
    return yesterday


def windsor_get(connector, fields, accounts=None, date_from=None, date_to=None,
                 date_preset=None, extra_params=None, retries=3):
    params = {
        "api_key": API_KEY,
        "fields": ",".join(fields),
    }
    if accounts:
        # IMPORTANTE: "account_id" no es un parámetro de filtro válido en la
        # API REST de Windsor.ai (solo es un campo de datos más, como
        # "campaign" o "date"). Para restringir a una cuenta concreta hay
        # que usar el mecanismo genérico de filtros, o si no, Windsor
        # devuelve TODAS las cuentas conectadas de ese conector mezcladas
        # -- bug real detectado el 06/09/2026 (cifras ~20x infladas).
        acc_list = accounts if isinstance(accounts, list) else [accounts]
        if len(acc_list) == 1:
            params["filter"] = json.dumps([["account_id", "eq", acc_list[0]]])
        else:
            params["filter"] = json.dumps([["account_id", "in", json.dumps(acc_list)]])
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if date_preset:
        params["date_preset"] = date_preset
    if extra_params:
        params.update(extra_params)

    url = f"{WINDSOR_BASE}/{connector}"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=90)
            resp.raise_for_status()
            data = resp.json()
            # Windsor devuelve {"data": [...]} normalmente
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            if isinstance(data, list):
                return data
            raise ValueError(f"Respuesta inesperada de Windsor: {data!r}"[:500])
        except Exception as e:
            last_err = e
            print(f"  [aviso] intento {attempt}/{retries} falló para {connector}: {e}", file=sys.stderr)
            time.sleep(3 * attempt)
    raise RuntimeError(f"Fallo definitivo pidiendo {connector}: {last_err}")


def to_float(v):
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# FORMULAS -- idénticas en todo el dashboard, SIEMPRE sobre agregados
# (SUM/SUM), nunca como media de tasas diarias.
# ---------------------------------------------------------------------------
def derive(agg):
    spend = agg["spend"]
    revenue = agg["revenue"]
    purchases = agg["purchases"]
    leads = agg["leads"]
    outbound = agg["outbound"]
    impressions = agg["impressions"]
    reach = agg["reach"]
    visits = agg.get("visits", 0)

    def safe_div(a, b):
        return (a / b) if b else 0.0

    return {
        **agg,
        "roas": safe_div(revenue, spend),
        "cpm": safe_div(spend, impressions) * 1000,
        "cpc": safe_div(spend, outbound),
        "ctr": safe_div(outbound, impressions) * 100,
        "cr_lead": safe_div(leads, outbound) * 100,
        "cr_purchase": safe_div(purchases, outbound) * 100,
        "cpl": safe_div(spend, leads),
        "cpa": safe_div(spend, purchases),
        "frequency": safe_div(impressions, reach),
        "aov": safe_div(revenue, purchases),
    }


def empty_agg():
    return {"spend": 0.0, "revenue": 0.0, "purchases": 0.0, "leads": 0.0,
            "outbound": 0.0, "impressions": 0.0, "reach": 0.0, "visits": 0.0}


# ---------------------------------------------------------------------------
# META (Facebook/Instagram) -- campos base per playbook original
# ---------------------------------------------------------------------------
META_FIELDS = [
    "date", "spend", "action_values_omni_purchase", "actions_omni_purchase",
    "actions_lead", "outbound_clicks_outbound_click", "impressions", "reach",
    "actions_landing_page_view",
]


def meta_row_to_agg(rows):
    agg = empty_agg()
    for r in rows:
        agg["spend"] += to_float(r.get("spend"))
        agg["revenue"] += to_float(r.get("action_values_omni_purchase"))
        agg["purchases"] += to_float(r.get("actions_omni_purchase"))
        agg["leads"] += to_float(r.get("actions_lead"))
        agg["outbound"] += to_float(r.get("outbound_clicks_outbound_click"))
        agg["impressions"] += to_float(r.get("impressions"))
        agg["reach"] += to_float(r.get("reach"))
        agg["visits"] += to_float(r.get("actions_landing_page_view"))
    return agg


def fetch_meta_period_agg(date_from, date_to):
    rows = windsor_get("facebook", META_FIELDS, accounts=[META_ACCOUNT],
                        date_from=date_from, date_to=date_to)
    return meta_row_to_agg(rows)


def build_meta_data(period_to_iso):
    print("== META: construyendo dataset ==")
    period_from = PERIOD_FROM_META

    # --- daily raw (una sola llamada, dimensión date) ---
    print("  fetch dailyRaw...")
    daily_rows = windsor_get("facebook", META_FIELDS, accounts=[META_ACCOUNT],
                              date_from=period_from, date_to=period_to_iso)
    daily_by_date = {}
    for r in daily_rows:
        d = r.get("date")
        if not d:
            continue
        a = daily_by_date.setdefault(d, empty_agg())
        a["spend"] += to_float(r.get("spend"))
        a["revenue"] += to_float(r.get("action_values_omni_purchase"))
        a["purchases"] += to_float(r.get("actions_omni_purchase"))
        a["leads"] += to_float(r.get("actions_lead"))
        a["outbound"] += to_float(r.get("outbound_clicks_outbound_click"))
        a["impressions"] += to_float(r.get("impressions"))
        a["reach"] += to_float(r.get("reach"))
        a["visits"] += to_float(r.get("actions_landing_page_view"))
    dailyRaw = [{"date": d, **a} for d, a in sorted(daily_by_date.items())]

    # --- last7d / prev7d (llamadas separadas, sin dimension date, para
    #     que frequency/reach sean exactos por periodo, no sumados) ---
    print("  fetch last7d/prev7d...")
    to_d = date.fromisoformat(period_to_iso)
    last7_from = (to_d - timedelta(days=6)).isoformat()
    prev7_to = (to_d - timedelta(days=7)).isoformat()
    prev7_from = (to_d - timedelta(days=13)).isoformat()
    last7d = derive(fetch_meta_period_agg(last7_from, period_to_iso))
    last7d["from"] = last7_from
    last7d["to"] = period_to_iso
    prev7d = derive(fetch_meta_period_agg(prev7_from, prev7_to))
    prev7d["from"] = prev7_from
    prev7d["to"] = prev7_to

    # --- meses (una llamada por mes, sin dimension date) ---
    print("  fetch mensual (rows)...")
    months = []
    rows_month = {}
    cur = date.fromisoformat(period_from).replace(day=1)
    while cur <= to_d:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        m_to = min(nxt - timedelta(days=1), to_d)
        label = cur.strftime("%b").capitalize()
        months.append(label)
        rows_month[label] = derive(fetch_meta_period_agg(cur.isoformat(), m_to.isoformat()))
        cur = nxt
    total_rows_month = empty_agg()
    for label in months:
        for k in total_rows_month:
            total_rows_month[k] += rows_month[label][k]
    rows_month["Total"] = derive(total_rows_month)

    # --- semanas (lunes-domingo, S1 = semana que contiene period_from) ---
    print("  fetch semanal (weekly)...")
    first_monday = date.fromisoformat(period_from) - timedelta(
        days=date.fromisoformat(period_from).weekday())
    weekly_labels = []
    weekly_rows = {}
    wk_start = first_monday
    wk_num = 1
    while wk_start <= to_d:
        wk_end = min(wk_start + timedelta(days=6), to_d)
        label = f"S{wk_num}"
        weekly_labels.append(label)
        weekly_rows[label] = derive(fetch_meta_period_agg(wk_start.isoformat(), wk_end.isoformat()))
        wk_start += timedelta(days=7)
        wk_num += 1
    total_week = empty_agg()
    for label in weekly_labels:
        for k in total_week:
            total_week[k] += weekly_rows[label][k]
    weekly_rows["Total"] = derive(total_week)

    # --- campañas (date + campaign) ---
    print("  fetch campañas...")
    camp_rows = windsor_get("facebook", META_FIELDS + ["campaign"],
                             accounts=[META_ACCOUNT],
                             date_from=period_from, date_to=period_to_iso)
    campaignsRaw = []
    campaigns_seen = set()
    for r in camp_rows:
        d = r.get("date")
        c = r.get("campaign") or "(sin nombre)"
        if not d:
            continue
        campaigns_seen.add(c)
        campaignsRaw.append([
            d, c,
            to_float(r.get("spend")),
            to_float(r.get("action_values_omni_purchase")),
            to_float(r.get("actions_omni_purchase")),
            to_float(r.get("actions_lead")),
            to_float(r.get("outbound_clicks_outbound_click")),
            to_float(r.get("impressions")),
            to_float(r.get("reach")),
            to_float(r.get("actions_landing_page_view")),
        ])

    # --- estado actual de campañas (activo/pausado) ---
    print("  fetch estado de campañas...")
    camp_status_rows = windsor_get("facebook", ["campaign", "campaign_effective_status"],
                                    accounts=[META_ACCOUNT],
                                    date_from=period_from, date_to=period_to_iso)
    campaignStatus = {}
    for r in camp_status_rows:
        c = r.get("campaign")
        s = r.get("campaign_effective_status")
        if c and s:
            campaignStatus[c] = s  # se queda con el último visto (más reciente en la respuesta)

    # --- adsets (date + campaign + adset) ---
    print("  fetch adsets...")
    ADSET_FIELDS = META_FIELDS + ["campaign", "adset_name", "adset_effective_status"]
    adset_rows = windsor_get("facebook", ADSET_FIELDS, accounts=[META_ACCOUNT],
                              date_from=period_from, date_to=period_to_iso)
    adsetsRaw = []
    adsetStatus = {}
    for r in adset_rows:
        d = r.get("date")
        c = r.get("campaign") or "(sin nombre)"
        a = r.get("adset_name") or "(sin nombre)"
        if not d:
            continue
        s = r.get("adset_effective_status")
        if s:
            adsetStatus[a] = s
        adsetsRaw.append([
            d, c, a,
            to_float(r.get("spend")),
            to_float(r.get("action_values_omni_purchase")),
            to_float(r.get("actions_omni_purchase")),
            to_float(r.get("actions_lead")),
            to_float(r.get("outbound_clicks_outbound_click")),
            to_float(r.get("impressions")),
            to_float(r.get("reach")),
            to_float(r.get("actions_landing_page_view")),
        ])

    # --- anuncios/creatividades (date + campaign + adset + ad) ---
    # Hook Rate = reproducciones a 2s / Impresiones; Hold Rate = ThruPlays / reproducciones a 2s
    # (definición aproximada; Meta no expone un campo literal "3 segundos" en Windsor.ai).
    print("  fetch anuncios/creatividades...")
    AD_FIELDS = META_FIELDS + [
        "campaign", "adset_name", "ad_name", "effective_status",
        "thumbnail_url", "website_destination_url",
        "video_continuous_2_sec_watched_actions_video_view",
        "video_thruplay_watched_actions_video_view",
    ]
    ad_rows = windsor_get("facebook", AD_FIELDS, accounts=[META_ACCOUNT],
                           date_from=period_from, date_to=period_to_iso)
    adsRaw = []
    adStatus = {}
    adMeta = {}
    for r in ad_rows:
        d = r.get("date")
        c = r.get("campaign") or "(sin nombre)"
        aset = r.get("adset_name") or "(sin nombre)"
        ad = r.get("ad_name") or "(sin nombre)"
        if not d:
            continue
        if r.get("effective_status"):
            adStatus[ad] = r.get("effective_status")
        if ad not in adMeta:
            adMeta[ad] = {
                "thumbnail_url": r.get("thumbnail_url") or "",
                "destination_url": r.get("website_destination_url") or "",
            }
        # Guardamos los NUMERADORES en bruto (vistas a 2s, thruplays), no la tasa ya
        # calculada -- así el front-end puede agregar por cualquier rango de fechas
        # (SUM/SUM), igual que hace con CTR/CPC/etc., nunca como media de tasas diarias.
        adsRaw.append([
            d, c, aset, ad,
            to_float(r.get("spend")),
            to_float(r.get("action_values_omni_purchase")),
            to_float(r.get("actions_omni_purchase")),
            to_float(r.get("actions_lead")),
            to_float(r.get("outbound_clicks_outbound_click")),
            to_float(r.get("impressions")),
            to_float(r.get("reach")),
            to_float(r.get("actions_landing_page_view")),
            to_float(r.get("video_continuous_2_sec_watched_actions_video_view")),
            to_float(r.get("video_thruplay_watched_actions_video_view")),
        ])

    # --- histórico 2025 (para Comparativa) ---
    print("  fetch dailyRaw2025...")
    to_2025 = date.fromisoformat(period_to_iso).replace(year=2025)
    rows_2025 = windsor_get("facebook", META_FIELDS, accounts=[META_ACCOUNT],
                             date_from=PERIOD_FROM_2025_META, date_to=to_2025.isoformat())
    daily_2025_by_date = {}
    for r in rows_2025:
        d = r.get("date")
        if not d:
            continue
        a = daily_2025_by_date.setdefault(d, empty_agg())
        a["spend"] += to_float(r.get("spend"))
        a["revenue"] += to_float(r.get("action_values_omni_purchase"))
        a["purchases"] += to_float(r.get("actions_omni_purchase"))
        a["leads"] += to_float(r.get("actions_lead"))
        a["outbound"] += to_float(r.get("outbound_clicks_outbound_click"))
        a["impressions"] += to_float(r.get("impressions"))
        a["reach"] += to_float(r.get("reach"))
        a["visits"] += to_float(r.get("actions_landing_page_view"))
    dailyRaw2025 = [{"date": d, **a} for d, a in sorted(daily_2025_by_date.items())]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_from": period_from,
        "period_to": period_to_iso,
        "dailyRaw2025": dailyRaw2025,
        "campaignsRaw": campaignsRaw,
        "campaignsList": sorted(campaigns_seen),
        "campaignStatus": campaignStatus,
        "adsetsRaw": adsetsRaw,
        "adsetStatus": adsetStatus,
        "adsRaw": adsRaw,
        "adStatus": adStatus,
        "adMeta": adMeta,
        "months": months,
        "last7d": last7d,
        "prev7d": prev7d,
        "weekly": {"labels": weekly_labels, "rows": weekly_rows},
        "dailyRaw": dailyRaw,
        "rows": rows_month,
    }


# ---------------------------------------------------------------------------
# TIKTOK -- campos base + mapeo a nombres internos (idéntico al de Meta)
# IMPORTANTE: leads = user_registration, NUNCA "form" (form siempre da 0
# en esta cuenta -- ver playbook histórico).
# ---------------------------------------------------------------------------
TT_FIELDS = [
    "date", "spend", "total_complete_payment_rate", "complete_payment",
    "user_registration", "clicks", "impressions", "reach",
    "total_landing_page_view",
]


def tt_row_to_agg(rows):
    agg = empty_agg()
    for r in rows:
        agg["spend"] += to_float(r.get("spend"))
        agg["revenue"] += to_float(r.get("total_complete_payment_rate"))
        agg["purchases"] += to_float(r.get("complete_payment"))
        agg["leads"] += to_float(r.get("user_registration"))
        agg["outbound"] += to_float(r.get("clicks"))
        agg["impressions"] += to_float(r.get("impressions"))
        agg["reach"] += to_float(r.get("reach"))
        agg["visits"] += to_float(r.get("total_landing_page_view"))
    return agg


def build_tt_data(period_to_iso):
    print("== TIKTOK: construyendo dataset ==")
    period_from = PERIOD_FROM_TT
    to_d = date.fromisoformat(period_to_iso)

    # TikTok: trocear por mes natural (timeout >1 mes con dimension date)
    print("  fetch dailyRaw (por mes)...")
    daily_by_date = {}
    cur = date.fromisoformat(period_from).replace(day=1)
    while cur <= to_d:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        m_to = min(nxt - timedelta(days=1), to_d)
        rows = windsor_get("tiktok", TT_FIELDS, accounts=[TT_ACCOUNT],
                            date_from=cur.isoformat(), date_to=m_to.isoformat())
        for r in rows:
            d = r.get("date")
            if not d:
                continue
            a = daily_by_date.setdefault(d, empty_agg())
            a["spend"] += to_float(r.get("spend"))
            a["revenue"] += to_float(r.get("total_complete_payment_rate"))
            a["purchases"] += to_float(r.get("complete_payment"))
            a["leads"] += to_float(r.get("user_registration"))
            a["outbound"] += to_float(r.get("clicks"))
            a["impressions"] += to_float(r.get("impressions"))
            a["reach"] += to_float(r.get("reach"))
            a["visits"] += to_float(r.get("total_landing_page_view"))
        cur = nxt
    dailyRaw = [{"date": d, **a} for d, a in sorted(daily_by_date.items())]

    # last7d/prev7d: agregados client-side desde dailyRaw ya fusionado
    # (simplificación deliberada, ver playbook: reach semanal de TikTok es
    # una SUMA de reach diario, no un reach exacto por periodo).
    def agg_range(daily_map, d_from, d_to):
        acc = empty_agg()
        for d, a in daily_map.items():
            if d_from <= d <= d_to:
                for k in acc:
                    acc[k] += a[k]
        return acc

    last7_from = (to_d - timedelta(days=6)).isoformat()
    prev7_to = (to_d - timedelta(days=7)).isoformat()
    prev7_from = (to_d - timedelta(days=13)).isoformat()
    last7d = derive(agg_range(daily_by_date, last7_from, period_to_iso))
    last7d["from"] = last7_from
    last7d["to"] = period_to_iso
    prev7d = derive(agg_range(daily_by_date, prev7_from, prev7_to))
    prev7d["from"] = prev7_from
    prev7d["to"] = prev7_to

    # meses y semanas: agregados client-side desde dailyRaw
    months = []
    rows_month = {}
    cur = date.fromisoformat(period_from).replace(day=1)
    while cur <= to_d:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        m_to = min(nxt - timedelta(days=1), to_d)
        label = cur.strftime("%b").capitalize()
        months.append(label)
        rows_month[label] = derive(agg_range(daily_by_date, cur.isoformat(), m_to.isoformat()))
        cur = nxt
    total_rows_month = empty_agg()
    for label in months:
        for k in total_rows_month:
            total_rows_month[k] += rows_month[label][k]
    rows_month["Total"] = derive(total_rows_month)

    first_monday = date.fromisoformat(period_from) - timedelta(
        days=date.fromisoformat(period_from).weekday())
    weekly_labels = []
    weekly_rows = {}
    wk_start = first_monday
    wk_num = 1
    while wk_start <= to_d:
        wk_end = min(wk_start + timedelta(days=6), to_d)
        label = f"S{wk_num}"
        weekly_labels.append(label)
        weekly_rows[label] = derive(agg_range(daily_by_date, wk_start.isoformat(), wk_end.isoformat()))
        wk_start += timedelta(days=7)
        wk_num += 1
    total_week = empty_agg()
    for label in weekly_labels:
        for k in total_week:
            total_week[k] += weekly_rows[label][k]
    weekly_rows["Total"] = derive(total_week)  # NUNCA olvidar esta fila (rompe la pestaña si falta)

    # campañas: date + campaign, troceado por mes
    print("  fetch campañas TikTok (por mes)...")
    campaignsRaw = []
    campaigns_seen = set()
    cur = date.fromisoformat(period_from).replace(day=1)
    while cur <= to_d:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        m_to = min(nxt - timedelta(days=1), to_d)
        rows = windsor_get("tiktok", TT_FIELDS + ["campaign"], accounts=[TT_ACCOUNT],
                            date_from=cur.isoformat(), date_to=m_to.isoformat())
        for r in rows:
            d = r.get("date")
            c = r.get("campaign") or "(sin nombre)"
            if not d:
                continue
            campaigns_seen.add(c)
            campaignsRaw.append([
                d, c,
                to_float(r.get("spend")),
                to_float(r.get("total_complete_payment_rate")),
                to_float(r.get("complete_payment")),
                to_float(r.get("user_registration")),
                to_float(r.get("clicks")),
                to_float(r.get("impressions")),
                to_float(r.get("reach")),
                to_float(r.get("total_landing_page_view")),
            ])
        cur = nxt

    # dailyRaw2025 (por mes/trimestre, cambia poco)
    print("  fetch dailyRaw2025 TikTok...")
    daily2025_by_date = {}
    to_2025 = date.fromisoformat(period_to_iso).replace(year=2025)
    cur = date.fromisoformat(PERIOD_FROM_2025_TT).replace(day=1)
    while cur <= to_2025:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        m_to = min(nxt - timedelta(days=1), to_2025)
        rows = windsor_get("tiktok", TT_FIELDS, accounts=[TT_ACCOUNT],
                            date_from=cur.isoformat(), date_to=m_to.isoformat())
        for r in rows:
            d = r.get("date")
            if not d:
                continue
            a = daily2025_by_date.setdefault(d, empty_agg())
            a["spend"] += to_float(r.get("spend"))
            a["revenue"] += to_float(r.get("total_complete_payment_rate"))
            a["purchases"] += to_float(r.get("complete_payment"))
            a["leads"] += to_float(r.get("user_registration"))
            a["outbound"] += to_float(r.get("clicks"))
            a["impressions"] += to_float(r.get("impressions"))
            a["reach"] += to_float(r.get("reach"))
            a["visits"] += to_float(r.get("total_landing_page_view"))
        cur = nxt
    dailyRaw2025 = [{"date": d, **a} for d, a in sorted(daily2025_by_date.items())]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_from": period_from,
        "period_to": period_to_iso,
        "dailyRaw2025": dailyRaw2025,
        "campaignsRaw": campaignsRaw,
        "campaignsList": sorted(campaigns_seen),
        "months": months,
        "last7d": last7d,
        "prev7d": prev7d,
        "weekly": {"labels": weekly_labels, "rows": weekly_rows},
        "dailyRaw": dailyRaw,
        "rows": rows_month,
    }


def sanity_check(meta, tt):
    """Verificación mínima antes de sobrescribir los JSON en producción."""
    errors = []
    if meta["rows"]["Total"]["spend"] <= 0:
        errors.append("META: inversión total <= 0, algo falló en el fetch.")
    if tt["last7d"]["leads"] == 0 and tt["last7d"]["outbound"] > 0:
        errors.append("TIKTOK: Leads=0 en last7d con clics>0 -- posible regresión "
                       "del campo 'form' en vez de 'user_registration'.")
    total_camp_spend = sum(r[2] for r in meta["campaignsRaw"])
    if abs(total_camp_spend - meta["rows"]["Total"]["spend"]) > max(1.0, meta["rows"]["Total"]["spend"] * 0.02):
        errors.append(f"META: inversión de campañas ({total_camp_spend:.2f}) no cuadra "
                       f"con el total mensual ({meta['rows']['Total']['spend']:.2f}).")
    return errors


def main():
    period_to = madrid_yesterday().isoformat()
    print(f"Actualizando dashboard FFJ hasta {period_to}...")

    meta = build_meta_data(period_to)
    tt = build_tt_data(period_to)

    errors = sanity_check(meta, tt)
    if errors:
        print("VERIFICACIÓN FALLIDA -- no se sobrescriben los JSON:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(2)

    with open(os.path.join(REPO_ROOT, "data_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    with open(os.path.join(REPO_ROOT, "data_tt.json"), "w", encoding="utf-8") as f:
        json.dump(tt, f, ensure_ascii=False)

    print(f"OK. data_meta.json y data_tt.json actualizados hasta {period_to}.")


if __name__ == "__main__":
    main()
