# Singer Customer Next-Product Recommendation App

This repository is ready for GitHub Pages.

## Included

- `index.html` — the recommendation app.
- `catalogue.json` — the saved Singer catalogue snapshot used by the page.
- `scripts/update_catalogue.py` — fetches public Singer Sri Lanka catalogue pages by SKU.
- `.github/workflows/update-and-deploy.yml` — refreshes Singer data hourly and deploys the site.

The existing recommendation engine, Excel upload/export, WhatsApp/SMS actions and English/Sinhala/Tamil customer messages are preserved. The browser-side proxy scraping was replaced with a GitHub Actions updater so the published page remains a static site.

## GitHub setup

1. Create a new GitHub repository and upload all files from this folder.
2. Use `main` as the default branch.
3. Open **Settings → Pages** and select **GitHub Actions** as the publishing source.
4. Open **Actions → Update Singer Catalogue & Deploy** and use **Run workflow** once.
5. Your Pages address will look like `https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/`.

The scheduled job runs once per hour at minute 7 UTC. GitHub scheduled workflows can be delayed during periods of high Actions load.

## `www.singersl.com`

The app reads public Singer Sri Lanka product information from `www.singersl.com`; it does not claim or host that domain.

You can only use `www.singersl.com` as your own custom GitHub Pages domain if you control that domain's DNS and have authorization to change it. For a domain you control, configure the custom domain under **Settings → Pages** and add the DNS records at your domain provider.

## Local testing

Opening `index.html` directly on your computer still works with the saved fallback catalogue. The automatic catalogue file is intended to be loaded from GitHub Pages or another web server.

## Data behavior

- Price/offer data is refreshed from public Singer catalogue pages.
- If a specific lookup fails, the saved value is retained rather than blanking the product.
- Stock is not used as a hard recommendation filter, matching the application's existing methodology.
