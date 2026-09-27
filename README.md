# Singer Customer Recommendation Hub

This repository contains the browser-based Singer customer next-product recommendation and sales action tool.

The published page is intended for internal sales decision support. It is a static GitHub Pages application: transaction files are analyzed in the user's browser and are not uploaded to the page.

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

## Custom domain

For a professional internal URL, a subdomain such as `app.singersl.com` is the cleaner choice than using `www.singersl.com`, because `www` normally serves the main public website.

To use `app.singersl.com`, configure that custom domain in the repository's GitHub Pages settings and add the required DNS record at the domain/DNS provider. DNS control and authorization for `singersl.com` are required. This repository intentionally does not claim ownership of that domain.

You can only use `www.singersl.com` as your own custom GitHub Pages domain if you control that domain's DNS and have authorization to change it. For a domain you control, configure the custom domain under **Settings → Pages** and add the DNS records at your domain provider.

## Local testing

Opening `index.html` directly on your computer still works with the saved fallback catalogue. The automatic catalogue file is intended to be loaded from GitHub Pages or another web server.

## Production-use notes

- The GitHub Pages site is public unless access is separately restricted by your hosting architecture; client-side passwords are not a security boundary.
- Customer transaction files are processed locally in the browser and are not sent to the site by the application code.
- Do not upload customer data unless the operator is authorized to process it.
- The application deliberately keeps stock labels out of customer-facing WhatsApp/SMS messages.

## Data behavior

- Price/offer data is refreshed from public Singer catalogue pages.
- If a specific lookup fails, the saved value is retained rather than blanking the product.
- Stock is not used as a hard recommendation filter, matching the application's existing methodology.
