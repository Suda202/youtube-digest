# Feishu Feedback Worker

This Worker receives Feishu card interaction callbacks and writes feedback to
`feedback.json` on the GitHub `data` branch.

## Deploy

Active Feishu callback URL:

```text
https://youtube-digest-feedback-pages.pages.dev/
```

Deploy the active Cloudflare Pages callback:

```bash
cd worker
tmp_dir=$(mktemp -d)
cp src/index.js "$tmp_dir/_worker.js"
wrangler pages deploy "$tmp_dir" --project-name youtube-digest-feedback-pages
```

Deploy the fallback Worker:

```bash
cd worker
wrangler secret put GH_TOKEN
# Optional: set this if Feishu callback verification token is enabled.
wrangler secret put FEISHU_VERIFICATION_TOKEN
wrangler deploy
```

`GH_TOKEN` needs repository contents write access and must be configured for
both the Pages project and the fallback Worker.

## Feishu Setup

In the Feishu app console:

1. Enable bot capability.
2. Configure card interaction callback URL:
   `https://youtube-digest-feedback-pages.pages.dev/`
3. Keep `FEISHU_APP_ID` and `FEISHU_APP_SECRET` configured in GitHub Actions.

The digest must be sent by the Feishu app bot, not by a custom group webhook,
otherwise card button callbacks will not fire.

## Local Smoke Test

```bash
curl -X POST https://<your-worker>.workers.dev/ \
  -H "Content-Type: application/json" \
  -d '{"challenge":"test"}'
```

Expected:

```json
{"challenge":"test"}
```
