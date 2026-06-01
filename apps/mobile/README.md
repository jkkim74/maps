# MAPS Mobile

Independent Capacitor hybrid client for MAPS operations. The existing Jinja web
dashboard remains unchanged.

## Local development

Start the FastAPI server from the repository root:

```powershell
uvicorn main:app --reload
```

Start the mobile web client:

```powershell
cd apps/mobile
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8000` during local development.

## Device build

Set `VITE_API_BASE_URL` to the HTTPS URL of the deployed MAPS API, then create a
native project and synchronize the web build:

```powershell
Copy-Item .env.example .env
npm run cap:add:android
npm run cap:sync
```

Use `npm run cap:add:ios` on macOS for an iOS project.
