"""
CRM дашборд + Bitrix24 — версия для хостинга на Render.

Вебхук берётся из переменной окружения BITRIX_WEBHOOK
(задаётся в настройках Render, в код не пишется).

Локально тоже можно:
    export BITRIX_WEBHOOK="https://mavisgroup.bitrix24.by/rest/2110/xxxx/"
    pip install -r requirements.txt
    gunicorn server:app         (или: python server.py)
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
import time
import os

WEBHOOK = (os.environ.get("BITRIX_WEBHOOK") or "").rstrip("/")
if WEBHOOK:
    WEBHOOK += "/"

app = Flask(__name__)
CORS(app)

_STATUS_CACHE = {}


def bx(method, params=None, retries=3):
    if not WEBHOOK:
        raise RuntimeError("BITRIX_WEBHOOK не задан. Добавьте переменную окружения в настройках Render.")
    url = WEBHOOK + method
    for attempt in range(retries):
        try:
            r = requests.post(url, json=params or {}, timeout=30)
            data = r.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(0.6)
            continue
        if "error" in data:
            raise RuntimeError(f"{method}: {data.get('error')} — {data.get('error_description')}")
        time.sleep(0.3)
        return data.get("result")
    return None


def load_status_maps():
    global _STATUS_CACHE
    if _STATUS_CACHE:
        return _STATUS_CACHE
    try:
        rows = bx("crm.status.list", {"order": {"SORT": "ASC"},
                                      "select": ["ENTITY_ID", "STATUS_ID", "NAME"]}) or []
    except Exception:
        rows = []
    m = {}
    for row in rows:
        m[(row.get("ENTITY_ID", ""), str(row.get("STATUS_ID")))] = row.get("NAME")
    _STATUS_CACHE = m
    return m


def stage_name(entity_hint, code):
    if code in (None, "", "-"):
        return ""
    maps = load_status_maps()
    for (ent, c), name in maps.items():
        if str(c) == str(code) and (entity_hint in ent):
            return name
    for (ent, c), name in maps.items():
        if str(c) == str(code):
            return name
    return str(code)


def _pick_latest(deals):
    try:
        return sorted(deals, key=lambda d: d.get("DATE_CREATE", ""), reverse=True)[0]
    except Exception:
        return deals[0]


def find_deal_for_lead(lead_id, lead_title):
    select = ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "SOURCE_ID", "CATEGORY_ID"]

    try:
        deals = bx("crm.deal.list", {"filter": {"LEAD_ID": lead_id}, "select": select}) or []
        if deals:
            return _pick_latest(deals), "по LEAD_ID"
    except Exception:
        pass

    try:
        contacts = bx("crm.lead.contact.items.get", {"id": lead_id}) or []
        for c in contacts:
            cid = c.get("CONTACT_ID")
            if not cid:
                continue
            deals = bx("crm.deal.list", {"filter": {"CONTACT_ID": cid}, "select": select}) or []
            if deals:
                return _pick_latest(deals), "по контакту"
    except Exception:
        pass

    try:
        title = (lead_title or "").strip()
        if title and not title.startswith("+375") and "Входящий звонок" not in title and "Лид с формы" not in title:
            deals = bx("crm.deal.list", {"filter": {"%TITLE": title}, "select": select}) or []
            if deals:
                return _pick_latest(deals), "по названию"
    except Exception:
        pass

    return None, ""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/webhook-status")
def webhook_status():
    if not WEBHOOK:
        return jsonify({"ok": False, "error": "BITRIX_WEBHOOK не задан на сервере"})
    try:
        me = bx("profile")
        return jsonify({"ok": True, "user": me})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/enrich", methods=["POST"])
def enrich():
    payload = request.json or {}
    leads = payload.get("leads", [])
    out, errors = [], []

    for L in leads:
        lead_id = L.get("ID") or L.get("Id") or L.get("id")
        lead_title = L.get("Название лида") or L.get("TITLE") or ""
        deal, how = None, ""
        try:
            deal, how = find_deal_for_lead(lead_id, lead_title)
        except Exception as e:
            errors.append(f"Лид {lead_id}: {e}")

        out.append({
            "ID лида": lead_id or "",
            "ID сделки": (deal.get("ID") if deal else "-"),
            "Стадия": L.get("Стадия") or "",
            "Источник": L.get("Источник") or "",
            "Стадия сделки на дату": (stage_name("DEAL_STAGE", deal.get("STAGE_ID")) if deal else "-"),
            "Сумма сделка, BYN": (deal.get("OPPORTUNITY") if deal else ""),
            "Название лида": lead_title,
            "Имя": L.get("Имя") or "",
            "Дата создания ЛИДА": L.get("Дата создания") or "",
            "Ответственный": L.get("Ответственный") or "",
            "UTM Source": L.get("UTM Source") or "",
            "UTM Medium": L.get("UTM Medium") or "",
            "UTM Campaign": L.get("UTM Campaign") or "",
            "UTM Content": L.get("UTM Content") or "",
            "UTM Term": L.get("UTM Term") or "",
            "_how": how,
        })

    return jsonify({"rows": out, "errors": errors})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
