# Feishu Feedback Worker

This Worker receives Feishu card interaction callbacks and writes feedback to
`feedback.json` on the GitHub `data` branch.

## Deploy

```bash
cd worker
wrangler secret put GH_TOKEN
# Optional: set this if Feishu callback verification token is enabled.
wrangler secret put FEISHU_VERIFICATION_TOKEN
wrangler deploy
```

`GH_TOKEN` needs repository contents write access.

## Feishu Setup

In the Feishu app console:

1. Enable bot capability.
2. Configure card interaction callback URL:
   `https://<your-worker>.workers.dev/`
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
