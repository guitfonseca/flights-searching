# ✈️ Caçador de passagens

Varre preços a cada 6h saindo de **SP (GRU/CGH + Viracopos)**, com **Rio e BH como plano B** (só alerta se, somando o ônibus, sair pelo menos R$ 250 mais barato que SP). Manda alerta no **WhatsApp** (e/ou Telegram) e publica um painel no **GitHub Pages**.

Tudo de graça: GitHub Actions + Aviasales Data API (Travelpayouts) + CallMeBot (WhatsApp).

## Como funciona

1. Para cada origem × destino do `config.yml`, busca o menor ida e volta nos próximos 5 meses.
2. É **promo** quando o custo total fica abaixo do teto do destino **ou** 30% abaixo da mediana histórica da rota (a mediana começa a valer depois de ~5 dias de dados).
3. Manda tudo numa mensagem só no WhatsApp, sem repetir a mesma rota por 5 dias (a não ser que caia mais 5%).
4. Salva histórico em `data/` e o painel em `docs/board.json`.

## Setup (uns 15 min)

### 1. Token da API de preços
- Crie conta em **travelpayouts.com**, entre no programa **Aviasales**.
- Copie o token em *Profile → API token*.

### 2. WhatsApp (CallMeBot)
- Salve o número do CallMeBot nos contatos (o número atual está em callmebot.com/blog/free-api-whatsapp-messages).
- Mande pra ele, no WhatsApp: `I allow callmebot to send me messages`
- Ele responde com a sua **apikey**.

<details><summary>Preferir Telegram? (opcional, pode usar os dois)</summary>

- **@BotFather** → `/newbot` → copie o token, mande uma mensagem pro bot.
- Abra `https://api.telegram.org/bot<TOKEN>/getUpdates` e pegue o `chat.id`.
- Crie os secrets `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
</details>

### 3. Repositório
- Crie um repo (ex.: `cacador-de-passagens`) e suba estes arquivos.
- *Settings → Secrets and variables → Actions → New repository secret*:
  - `TRAVELPAYOUTS_TOKEN`
  - `WHATSAPP_PHONE` → seu número com DDI e DDD, sem `+` (ex.: `5511999998888`)
  - `CALLMEBOT_APIKEY`
- *Settings → Actions → General → Workflow permissions* → **Read and write**.
- *Settings → Pages* → Source: **Deploy from a branch**, branch `main`, pasta **/docs**.

### 4. Primeira rodada
- Aba **Actions → Caçar passagens → Run workflow**.
- Leva ~5–8 min. Depois o painel fica em `https://<seu-usuario>.github.io/<repo>/`.

## Ajustes no `config.yml`

| Quero… | Mexo em |
|---|---|
| Mudar quanto acho "barato" | `teto` do grupo ou do destino |
| Adicionar destino | uma linha `{ codigo: XXX, nome: ... }` (código IATA da cidade) |
| Ser mais/menos exigente com a queda | `regra_promo.queda_vs_mediana` |
| Atualizar custo do ônibus | `onibus` nas origens |
| Menos chamadas / rodada mais rápida | `meses_a_frente` |

Teste local sem mandar mensagem:
```bash
pip install -r requirements.txt
TRAVELPAYOUTS_TOKEN=xxx DRY_RUN=1 python flight_alerts.py
```

## Avisos
- O CallMeBot é gratuito e feito pra uso pessoal (mandar pra você mesmo). Por isso o script junta tudo numa mensagem por rodada.
- Os preços vêm do **cache de buscas** da Aviasales, então às vezes a tarifa já sumiu. Sempre confirme antes de comprar.
- O cron do GitHub pode atrasar alguns minutos em horário de pico.
