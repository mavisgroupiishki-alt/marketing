"""
CRM дашборд + Bitrix24 — версия для Render.
Вебхук берётся из переменной окружения BITRIX_WEBHOOK.

Обработка идёт ПАЧКАМИ: фронт присылает лидов по несколько штук,
для каждой пачки делается один пакетный вызов Bitrix (метод batch),
поэтому запрос укладывается в лимит времени Render.
"""

from flask import Flask, request, jsonify, render_template
import requests
import os

WEBHOOK = (os.environ.get("BITRIX_WEBHOOK") or "").rstrip("/")
if WEBHOOK:
    WEBHOOK += "/"

app = Flask(__name__)

_STATUS_CACHE = {}


def bx(method, params=None):
    if not WEBHOOK:
        raise RuntimeError("BITRIX_WEBHOOK не задан на сервере (переменная окружения).")
    r = requests.post(WEBHOOK + method, json=params or {}, timeout=25)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{method}: {data.get('error')} — {data.get('error_description')}")
    return data.get("result")


def bx_batch(cmds):
    if not WEBHOOK:
        raise RuntimeError("BITRIX_WEBHOOK не задан на сервере.")
    r = requests.post(WEBHOOK + "batch", json={"halt": 0, "cmd": cmds}, timeout=25)
    data = r.json()
    result = (data.get("result") or {})
    return result.get("result", {})


def load_status_maps():
    global _STATUS_CACHE
    if _STATUS_CACHE:
        return _STATUS_CACHE
    try:
        rows = bx("crm.status.list", {"select": ["ENTITY_ID", "STATUS_ID", "NAME"]}) or []
    except Exception:
        rows = []
    m = {}
    for row in rows:
        m[(row.get("ENTITY_ID", ""), str(row.get("STATUS_ID")))] = row.get("NAME")
    _STATUS_CACHE = m
    return m


def stage_name(code):
    if code in (None, "", "-"):
        return ""
    maps = load_status_maps()
    for (ent, c), name in maps.items():
        if str(c) == str(code) and "DEAL_STAGE" in ent:
            return name
    for (ent, c), name in maps.items():
        if str(c) == str(code):
            return name
    return str(code)


def _pick_latest(deals):
    try:
        return sorted(deals, key=lambda d: d.get("DATE_CREATE", ""), reverse=True)[0]
    except Exception:
        return deals[0] if deals else None


def enrich_batch(leads):
    select = ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "DATE_CREATE"]
    cmds = {}
    for i, L in enumerate(leads):
        lead_id = L.get("ID") or L.get("Id") or L.get("id")
        q = "crm.deal.list?" + f"filter[LEAD_ID]={lead_id}&" + "&".join([f"select[]={f}" for f in select])
        cmds[f"d{i}"] = q

    try:
        res = bx_batch(cmds) if cmds else {}
    except Exception:
        res = {}

    rows = []
    for i, L in enumerate(leads):
        lead_id = L.get("ID") or L.get("Id") or L.get("id")
        lead_title = L.get("Название лида") or L.get("TITLE") or ""
        deals = res.get(f"d{i}") or []
        deal = _pick_latest(deals) if deals else None
        how = "по LEAD_ID" if deal else ""

        rows.append({
            "ID лида": lead_id or "",
            "ID сделки": (deal.get("ID") if deal else "-"),
            "Стадия": L.get("Стадия") or "",
            "Источник": L.get("Источник") or "",
            "Стадия сделки на дату": (stage_name(deal.get("STAGE_ID")) if deal else "-"),
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
    return rows


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
    try:
        rows = enrich_batch(leads)
        return jsonify({"rows": rows})
    except Exception as e:
        return jsonify({"rows": [], "error": str(e)}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
