# Existing Grok / Omni Regression QC

Date: 2026-07-15. No new video generation was performed.

## Files

- Grok: `D:\video-jobs\shaved-ice-mexico-60s-grok-vs-omni-20260715\grok\shaved-ice-mexico-60s-grok.mp4`
- Omni: `D:\video-jobs\shaved-ice-mexico-60s-grok-vs-omni-20260715\omni\shaved-ice-mexico-60s-omni.mp4`
- Product reference: `D:\Desktop\微信图片_2026-06-23_194805_160.jpg`

## Local Technical QC

| Provider | Duration | Resolution | Decode | Black | Freeze | Local result |
|---|---:|---:|---|---|---|---|
| Grok | 60.205875s | 720x1280 | pass | none | none | pass |
| Omni | 60.051667s | 720x1280 | pass | none | none | pass |

## Product-Lock Review

Both must fail semantic QC despite passing technical QC.

- Grok starts with an unrelated countertop blender and later changes into
  several red/yellow hand-crank structures. It does not preserve the supplied
  red/yellow clear-cup product.
- Omni changes among a white appliance, a yellow transparent device and a large
  red commercial-looking machine. Product geometry and mechanism drift are
  obvious and stronger than Grok.
- The old prompts asked each independent segment to perform several actions and
  supplied no real visual reference or previous-tail context.

Expected semantic JSON for both has `passed=false` and `product_drift=true`.
Omni also has `wrong_mechanics=true`. These videos are regression evidence only
and must not be used as V4 final acceptance media.

