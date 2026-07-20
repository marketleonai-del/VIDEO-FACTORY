# Gem Routing Design

## Goal

Given a product-image folder, Codex should choose the best Gemini Gem for each
image before asking the Gem to generate prompts. This avoids sending every
product through one generic template and keeps the expensive generation stages
behind a local, inspectable routing decision.

## Adopted Pattern

The router follows the retrieve-and-rerank pattern used by modern agent routing
projects:

1. Keep a local registry of available Gems with IDs, names, aliases, URLs, and
   route metadata.
2. Convert the product image into non-sensitive routing text: filename, parent
   folder, product name, optional image caption/OCR.
3. Retrieve candidate Gems with deterministic keyword and alias matching.
4. Rerank candidates with product-category preference: `BV28-BV57` are product
   category Gems; `BV24` is the all-category fallback.
5. Call only the selected Gem. Record the selected Gem, score, and reason in the
   batch manifest and Feishu rows.

## Commands

Route a single image without calling Gemini:

```bash
batch-video-skill route-gem --image "D:\products\厨房清理剂.jpg" --product "厨房清理剂"
```

Dry-run a mixed folder and write per-image Gem route results:

```bash
batch-video-skill batch-folder "D:\products" --dry-run --max-items 5 --variants 1 --auto-gem-route
```

Live generation still needs explicit approval because it can spend API/video
credits:

```bash
batch-video-skill batch-folder "D:\products" --variants 1 --auto-gem-route --concurrency 1
```

## Routing Rules

- Product images are not sent to Gemini Gem. The local router uses text derived
  from the path/name/caption.
- If the filename or product text says `厨房清理剂`, `清洁剂`, `去污`, `油污`,
  `cleaner`, or `degreaser`, route to `BV42 / 带货视频42-清洁工具`.
- If a product category is clearly present, prefer the corresponding `BV28-BV57`
  category Gem.
- If a style is clearly present, such as `开箱`, `街头采访`, `教程`, or `前后对比`,
  include that style Gem as a candidate, but keep product-category Gems as the
  primary route for mixed ecommerce folders.
- If no category is clear, fall back to `BV24 / 全类目通用`.

## Evidence To Check

For dry-run or production batches, inspect:

- `batch_manifest.csv`: `gem_id`, `gem_name`, `gem_route_score`,
  `gem_route_reason`
- `gem_requests/*.txt`: the selected category and product text sent to Gem
- `state/*.json`: current per-job status and route metadata
- Feishu rows after sync: selected Gem and task status should be visible

## Future Upgrade

The current router is deterministic and offline. If more accuracy is needed,
add an optional visual-caption stage before routing:

1. Use a local/approved vision model or manual caption to create one sentence:
   `product_type`, `visible_use`, `material`, `likely_scene`.
2. Pass it to `route-gem --caption`.
3. Keep the route score and reasons in the manifest for audit.

Do not add a hidden paid vision call to routing; make it an explicit option.
