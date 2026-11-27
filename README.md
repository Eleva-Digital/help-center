# Eleva Help Center

A modern, white-labeled help center for Eleva clients, in English and Portuguese.

## How it works
- `scripts/sync.py` reads the platform's public knowledge base, rewrites the branding to Eleva,
  hides agency-only categories, and saves every article as a small JSON file in `docs/data/`.
- **GitHub Pages** serves `docs/` for free at `https://eleva-digital.github.io/help-center/`.
- **GitHub Actions** runs the sync automatically on the 1st of every month and saves only the articles that changed.
  You can also run it any time: **Actions → Monthly help center sync → Run workflow**.
- The page in the GHL funnel (`ghl-custom-code.html`) loads the article list and each article from GitHub Pages,
  so the funnel stays small and fast.

## What you can change
Edit `config.json` (you can edit it right on github.com with the pencil icon):
- `hideCategories`: categories clients should not see.
- `renameCategories`: rename a category.
- `brand`, `appDomain`, `siteDomain`: the names and links used when rebranding.
Changes take effect on the next sync (or run the workflow manually).

## Files
| Path | What it is |
|---|---|
| `src/help.src.html` + `build.py` | Page source. Run `python3 build.py` after editing. |
| `ghl-custom-code.html` | Paste into the GHL Custom Code element. |
| `docs/index.html` | Standalone copy of the page on GitHub Pages (backup). |
| `docs/data/` | Synced articles (generated, do not edit by hand). |
| `.github/workflows/sync.yml` | The monthly auto-update. |

## Safety
- This repository holds only public help content: no passwords, keys or client data. Keep it that way.
- No secrets are needed: the sync reads public pages, and the workflow uses GitHub's built-in token to save changes.
- The sync refuses to save if most articles suddenly fail to parse (protects against a broken source page).
