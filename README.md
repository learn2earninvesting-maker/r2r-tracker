# R2R Financial Tracker — client edition

Static PWA for GitHub Pages. Contains no client data.

- `index.html` — the dashboard. Set `API_URL` near the top of the script to your Apps Script web-app URL (ends in `/exec`).
- `demo-data.json` — fictional demo family, shown at `?demo=1`.
- `sw.js` — offline shell. Bump `CACHE` when you deploy changes.

Clients open the personal link created by the registry sheet (R2R → Add new client).
