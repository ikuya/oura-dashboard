# Oura Dashboard

## Timezone handling

Data from the Oura API is stored in the DB as UTC timestamps. When displaying data, always convert to the user's local timezone (browser locale on the frontend, system locale on the backend). Do not convert at the DB storage layer.

- Frontend (Chart.js): use `chartjs-adapter-luxon` with `adapters: { date: { zone: Intl.DateTimeFormat().resolvedOptions().timeZone } }` on time axes that plot UTC timestamps
- Date-only axes (plotting `YYYY-MM-DD` strings): use `adapters: { date: { zone: "UTC" } }` to prevent unintended date shifts
