# Weather SIP Alerter (NWS + FlyNumber + Fly.io)

Automated Python service that polls the [National Weather Service API](https://www.weather.gov/documentation/services-web-api) for active weather alerts, generates a custom 8kHz mono `.wav` text-to-speech announcement, pulls recipients from your Google Sheet or `contacts.csv`, and places outbound SIP calls through [FlyNumber SIP](https://www.flynumber.com/docs/basic-settings/flynumber-sip), packaged for [Fly.io](https://docs.fly.io/reference/configuration) with continuous deployment via [GitHub Actions](https://docs.fly.io/launch/continuous-deployment-with-github-actions).

## Repository Contents

- `nws_sip_alerter.py` — Main polling, TTS audio generation, and SIP calling worker
- `Dockerfile` — Python 3.12 container with `ffmpeg`, `espeak-ng`, and `baresip`
- `fly.toml` — Fly.io app configuration and persistent `/data` volume mount
- `.github/workflows/fly-deploy.yml` — Auto-deploys to Fly.io on pushes to `main`
- `requirements.txt` — Python dependencies (`requests`, `gTTS`, `pydub`)
- `contacts.csv` — Local fallback contact list
- `.env.example` — Environment variable template

## 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial weather SIP alerter"
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/<YOUR_REPO>.git
git push -u origin main
```

## 2. Create the Fly.io App & Volume

```bash
fly launch --no-deploy
fly volumes create alerter_data --region dfw --size 1
```

Set your FlyNumber SIP secrets (never commit passwords to GitHub):

```bash
fly secrets set \
  FLYNUMBER_SIP_USER="your_sip_username" \
  FLYNUMBER_SIP_PASSWORD="your_sip_password" \
  FLYNUMBER_CALLER_ID="16625550100" \
  GOOGLE_SHEET_CSV_URL="https://docs.google.com/spreadsheets/d/1--0KsZQHGzJ2QnmdN2tWhZagxVaAz1Ejc4DkssaTFUE/export?format=csv"
```

When ready to place live calls instead of dry-run logs:

```bash
fly secrets set DRY_RUN="false"
```

## 3. Enable GitHub Actions Auto-Deploy

1. Generate a Fly deploy token:
   ```bash
   fly tokens create deploy -x 999999h
   ```
2. In your GitHub repository, open **Settings → Secrets and variables → Actions** and create a secret named `FLY_API_TOKEN` with that token.
3. Every push to `main` will automatically run `.github/workflows/fly-deploy.yml` and deploy to Fly.io.

## Important FlyNumber & Contact Notes

- **Active FlyNumber & Balance:** Outbound SIP calls require an active FlyNumber, **Enable outbound calls** turned on in your panel, and a prepaid balance above `$0` ([FlyNumber SIP Setup](https://www.flynumber.com/docs/basic-settings/flynumber-sip)).
- **Autodialing Policy:** FlyNumber's SIP documentation states that telemarketing, cold calling, and autodialing are not permitted on FlyNumber SIP outbound lines; verify your use case complies before enabling `DRY_RUN=false`.
- **Contact Format:** Outbound US dialing requires 11-digit numbers (`1` + 10 digits) rather than internal PBX extensions.
