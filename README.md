# TikTok Downloader

Automatically downloads all new videos from a list of TikTok accounts. Tracks deletions, username changes, and stores metadata both in a database and embedded directly in each video file.

Managed through a small web UI. All state (users, cookies, download history) lives on the server, so the UI is just a window — open it from any device and it shows the same thing.

---

## How it works

A background loop runs on a fixed interval (default: 30 minutes, recommended: 3 hours for large libraries). Each iteration:

1. Loads all tracked users from the database
2. Creates one authenticated TikTok session (shared across all users in that run)
3. For each user:
   - Refreshes their profile info and detects username changes
   - Fetches their full public video list from TikTok
   - Compares it against the database
   - Downloads any new videos via yt-dlp, embedding metadata into the file
   - Marks any previously-seen videos that have disappeared as deleted
   - Marks any previously-deleted videos that have reappeared as restored

The first run for an account with hundreds of videos can take a long time — this is expected. Subsequent runs are much faster since only new content is downloaded.

---

## Prerequisites

- **Docker** and **Docker Compose** (for the recommended deployment)
- A TikTok account and a way to export its cookies (see below)
- A reverse proxy such as **Caddy** or **nginx** if you want to expose the UI over the internet

---

## Getting your TikTok cookies

TikTokApi (used to browse video lists) and yt-dlp (used to download) both need your TikTok session cookies to work. These are read from a single `cookies.txt` file in Netscape format.

**How to export:**

1. Install the browser extension **Get cookies.txt LOCALLY**
   ([Chrome](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) / [Firefox](https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/))
2. Log in to [TikTok](https://www.tiktok.com) in your browser
3. Click the extension icon while on tiktok.com and export — save the file as `cookies.txt`
4. Upload it through the web UI (see below)

> Cookies expire periodically. If downloads start failing, re-export and re-upload.

---

## Setup

### 1. Get the docker-compose file

Create a folder on your server and drop in a `docker-compose.yml`:

```yaml
services:
  tiktok-downloader:
    image: ghcr.io/OWNER/tiktok-downloader:latest
    container_name: tiktok-downloader
    restart: unless-stopped
    volumes:
      - ./data:/app/data      # database + cookies.txt
      - ./videos:/app/videos  # downloaded video files
    environment:
      LOOP_INTERVAL_MINUTES: "180"
    ports:
      - "127.0.0.1:5000:5000"
```

Replace `ghcr.io/OWNER/tiktok-downloader:latest` with the actual published image name.

### 2. Start the container

```bash
docker compose up -d
```

The web UI will be available at `http://localhost:5000` (or via your reverse proxy).

### 3. Upload cookies

Open the web UI and click **Upload cookies.txt** in the Authentication section. Select the file you exported from your browser. The status pill will turn green once it's stored.

### 4. Add accounts

Type a TikTok username (with or without `@`) into the **Track a user** field and click **Add**. The system will look up the account's permanent ID and store it. Add as many accounts as you like.

### 5. Wait or trigger a run

The loop runs automatically on startup and then on the interval you configured. Click **Run Now** in the header to trigger an immediate run without waiting.

---

## Caddy integration

If Caddy runs on the same host as Docker:

```caddy
tiktok.yourdomain.com {
    reverse_proxy localhost:5000
}
```

If Caddy is also a Docker container, put both on a shared network and remove the `ports` block from the compose file:

```yaml
# in docker-compose.yml
networks:
  - caddy_net         # must match the network Caddy is on

# remove the ports block, use expose instead:
expose:
  - "5000"
```

```caddy
tiktok.yourdomain.com {
    reverse_proxy tiktok-downloader:5000
}
```

---

## Building and publishing the image

```bash
# Build
docker build -t ghcr.io/OWNER/tiktok-downloader:latest .

# Push to GitHub Container Registry
docker push ghcr.io/OWNER/tiktok-downloader:latest
```

To authenticate with GHCR, see the [GitHub docs](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

---

## Configuration

All configuration is via environment variables in `docker-compose.yml`.

| Variable | Default | Description |
|---|---|---|
| `LOOP_INTERVAL_MINUTES` | `30` | Minutes between download loop runs. `180` is recommended for large libraries. |
| `WEB_PORT` | `5000` | Port the web UI listens on inside the container. |
| `DATA_DIR` | `/app/data` | Where the database and cookies.txt are stored. |
| `VIDEOS_DIR` | `/app/videos` | Where downloaded videos are saved. |
| `ms_token` | — | Fallback: provide the raw `msToken` cookie value if not using a cookies file. |

---

## Data layout

```
./data/
  tiktok.db        # SQLite database — users, videos, username history
  cookies.txt      # TikTok session cookies (uploaded via UI)
  logs/
    transcript.log # Daily-rotating full output log

./videos/
  @username/
    1234567890.mp4  # Each video named by its TikTok video ID
    ...
```

### What's stored in the database

**Users:** TikTok ID, current username, display name, bio, follower/following/video counts, join date, account status (active / banned), date added, date last checked.

**Username history:** Every username change is recorded with a timestamp, so you always know what an account used to be called.

**Videos:** Video ID, type (video or photo carousel), description, upload date, download date, file path, and status — `up`, `deleted` (disappeared from TikTok), or `undeleted` (reappeared after being deleted), with timestamps for each status change.

### Metadata embedded in video files

Each downloaded MP4 has the following tags written by ffmpeg:

| Tag | Content |
|---|---|
| `title` | Post description |
| `artist` | Username at time of download |
| `album_artist` | Display name |
| `date` | Upload date (YYYY-MM-DD) |
| `comment` | Pipe-separated key=value string with video ID, author ID, username, display name, upload date, and download date |
