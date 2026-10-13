"""Caçador de passagens baratas saindo de SP (e, em último caso, RJ/BH).

Roda no GitHub Actions: busca preços na Aviasales Data API (Travelpayouts),
compara com teto e histórico, manda alerta no Telegram e atualiza o painel.
"""
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
WPP_PHONE = os.environ.get("WHATSAPP_PHONE")      # ex.: 5511999998888
WPP_KEY = os.environ.get("CALLMEBOT_APIKEY")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
TODAY = dt.date.today().isoformat()


def load(rel, default):
    p = ROOT / rel
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def save(rel, data):
    (ROOT / rel).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def brl(v):
    return "R$ " + f"{v:,.0f}".replace(",", ".")


def months_ahead(n):
    d = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(d.strftime("%Y-%m"))
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return out


def fetch(origin, dest, month):
    params = {
        "origin": origin, "destination": dest, "departure_at": month,
        "one_way": "false", "direct": "false", "sorting": "price",
        "unique": "false", "currency": "brl", "market": "br",
        "limit": 10, "page": 1, "token": TOKEN,
    }
    for attempt in range(3):
        try:
            r = requests.get(API, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json().get("data") or []
        except requests.RequestException as e:
            print(f"  ! {origin}->{dest} {month}: {e}", file=sys.stderr)
            time.sleep(3)
    return []


def links(t):
    o = t.get("origin_airport") or t["origin"]
    d = t.get("destination_airport") or t["destination"]
    dep = t["departure_at"][:10]
    ret = (t.get("return_at") or "")[:10]
    q = f"Flights from {o} to {d} on {dep}" + (f" through {ret}" if ret else "")
    gf = "https://www.google.com/travel/flights?hl=pt-BR&curr=BRL&q=" + urllib.parse.quote(q)
    av = "https://www.aviasales.com" + t["link"] if t.get("link") else None
    return av, gf


def fmt_date(iso):
    return dt.date.fromisoformat(iso[:10]).strftime("%d/%m/%y") if iso else "?"


def main():
    if not TOKEN:
        sys.exit("Falta TRAVELPAYOUTS_TOKEN.")
    cfg = yaml.safe_load((ROOT / "config.yml").read_text("utf-8"))
    busca, regra = cfg["busca"], cfg["regra_promo"]
    history = load("data/history.json", {})
    alerted = load("data/alerted.json", {})
    months = months_ahead(busca["meses_a_frente"])

    destinos = []
    for gname, g in cfg["grupos"].items():
        for d in g["destinos"]:
            destinos.append({**d, "grupo": gname, "teto": d.get("teto", g["teto"])})

    # 1) coleta: melhor bilhete por (origem, destino)
    best = {}
    for o in cfg["origens"]:
        for d in destinos:
            tickets = []
            for m in months:
                tickets += fetch(o["codigo"], d["codigo"], m)
                time.sleep(busca["pausa_entre_chamadas"])
            if not tickets:
                continue
            t = min(tickets, key=lambda x: x["price"])
            best[(o["codigo"], d["codigo"])] = {
                "t": t, "origem": o, "dest": d,
                "custo": t["price"] + o["onibus"],
            }
            print(f"{o['codigo']}->{d['codigo']}: {brl(t['price'])}")

    # 2) avaliação
    board, deals = [], []
    for (oc, dc), b in best.items():
        o, d, t = b["origem"], b["dest"], b["t"]
        key = f"{oc}-{dc}"

        # histórico: mínimo diário por rota (90 dias)
        h = [x for x in history.get(key, []) if x["date"] != TODAY]
        past = [x["min"] for x in h]
        h.append({"date": TODAY, "min": t["price"]})
        history[key] = h[-90:]
        mediana = statistics.median(past) if len(past) >= regra["min_dias_historico"] else None

        motivos = []
        if b["custo"] <= d["teto"]:
            motivos.append(f"abaixo do teto ({brl(d['teto'])})")
        if mediana and t["price"] <= mediana * (1 - regra["queda_vs_mediana"]):
            pct = round((1 - t["price"] / mediana) * 100)
            motivos.append(f"{pct}% abaixo do normal ({brl(mediana)})")

        # entre origens de SP, só a mais barata alerta; origem alternativa
        # (RJ/BH) só conta se bater SP com folga, já somando o ônibus
        sp = [x["custo"] for (oc2, dc2), x in best.items()
              if dc2 == dc and x["origem"]["principal"]]
        if o["principal"]:
            if b["custo"] > min(sp):
                motivos = []
        elif sp and b["custo"] > min(sp) - cfg["economia_minima_alternativa"]:
            motivos = []

        av, gf = links(t)
        row = {
            "origem": oc, "origem_nome": o["nome"], "destino": dc,
            "destino_nome": d["nome"], "grupo": d["grupo"],
            "preco": t["price"], "onibus": o["onibus"], "custo": b["custo"],
            "ida": t["departure_at"][:10], "volta": (t.get("return_at") or "")[:10],
            "escalas": t.get("transfers", 0), "cia": t.get("airline", ""),
            "mediana": mediana, "promo": bool(motivos), "motivos": motivos,
            "aviasales": av, "google": gf,
        }
        board.append(row)
        if motivos:
            deals.append(row)

    # 3) alertas (sem repetir)
    deals.sort(key=lambda r: r["custo"] / (r["mediana"] or r["custo"]))
    sent, to_send = 0, []
    for r in deals:
        key = f"{r['origem']}-{r['destino']}"
        prev = alerted.get(key)
        if prev:
            age = (dt.date.today() - dt.date.fromisoformat(prev["date"])).days
            caiu = r["preco"] <= prev["preco"] * (1 - busca["reavisar_se_cair"])
            if age < busca["cooldown_dias"] and not caiu:
                continue
        if sent >= busca["max_alertas_por_rodada"]:
            break
        to_send.append(r)
        alerted[key] = {"date": TODAY, "preco": r["preco"]}
        sent += 1

    if to_send:
        notify(to_send)

    board.sort(key=lambda r: (not r["promo"], r["grupo"] != "nacional", r["custo"]))
    save("data/history.json", history)
    save("data/alerted.json", alerted)
    save("docs/board.json", {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "routes": board,
    })
    print(f"\n{len(board)} rotas, {len(deals)} promoções, {sent} alertas enviados.")


def reais(v):
    # CallMeBot às vezes engole o "$" de "R$", então no WhatsApp vai por extenso
    return f"{v:,.0f}".replace(",", ".") + " reais"


def wpp_text(rows):
    parts = [f"✈️ *{len(rows)} passagem(ns) barata(s) encontrada(s)*"]
    for r in rows:
        escalas = "direto" if r["escalas"] == 0 else f"{r['escalas']} escala(s)"
        p = [
            f"*{r['origem_nome']} → {r['destino_nome']}*",
            f"💰 *{reais(r['preco'])}* ida e volta · {escalas} · {r['cia']}",
            f"📅 {fmt_date(r['ida'])} → {fmt_date(r['volta'])}",
        ]
        if r["onibus"]:
            p.append(f"🚌 + ~{reais(r['onibus'])} de ônibus = *{reais(r['custo'])}* no total")
        p.append("💡 " + re.sub(r"R\$ ([\d.]+)", r"\1 reais", "; ".join(r["motivos"])))
        p.append(r["google"])
        parts.append("\n".join(p))
    return "\n\n".join(parts)


def tg_text(r):
    escalas = "direto" if r["escalas"] == 0 else f"{r['escalas']} escala(s)"
    lines = [
        f"✈️ <b>{r['origem_nome']} → {r['destino_nome']}</b>",
        f"<b>{brl(r['preco'])}</b> ida e volta · {escalas} · {r['cia']}",
        f"📅 {fmt_date(r['ida'])} → {fmt_date(r['volta'])}",
    ]
    if r["onibus"]:
        lines.append(f"🚌 + ~{brl(r['onibus'])} de ônibus = <b>{brl(r['custo'])}</b> no total")
    lines.append("💡 " + "; ".join(r["motivos"]))
    lines.append(f'<a href="{r["google"]}">Google Flights</a>')
    return "\n".join(lines)


def notify(rows):
    if DRY_RUN or not ((WPP_PHONE and WPP_KEY) or (TG_TOKEN and TG_CHAT)):
        print("\n--- WHATSAPP ---\n" + wpp_text(rows))
        return

    if WPP_PHONE and WPP_KEY:
        # uma mensagem só por rodada (a API gratuita não gosta de rajadas)
        text = wpp_text(rows)
        chunks, cur = [], ""
        for block in text.split("\n\n"):
            if len(cur) + len(block) > 1500:
                chunks.append(cur); cur = ""
            cur += ("\n\n" if cur else "") + block
        chunks.append(cur)
        for c in chunks:
            r = requests.get("https://api.callmebot.com/whatsapp.php",
                             params={"phone": WPP_PHONE, "text": c, "apikey": WPP_KEY},
                             timeout=30)
            print("WhatsApp:", r.status_code)
            time.sleep(10)

    if TG_TOKEN and TG_CHAT:
        for r in rows:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": tg_text(r), "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=20,
            )


if __name__ == "__main__":
    main()
