Run the app with Docker:

1. Build and start:
   docker compose up --build

2. The SQLite database is stored in the Docker volume `app_data`.

3. The app uses `DB_NAME=/app/data/CyberXchange.db` inside the container.

Notes:
- This project uses Tkinter, so the container needs GUI/display access.
- On Linux, you can usually use your local X server.
- On Windows, Docker containers do not show Tkinter windows by default. You may need an X server such as VcXsrv, or run the app locally and keep Docker only for other services.
