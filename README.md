# MelodyHub — Flask Music Recommendation System

A fully responsive music web application where users can browse tracks by genre and mood, listen via a dedicated player page, manage personal playlists asynchronously (AJAX), track listening history with correct timezone support, and view interactive profile analytics of their music taste.

## Features

- **User Authentication** — Secure registration/login with password hashing (werkzeug)
- **Persistent Playlists** — Add/remove songs instantly via AJAX without page reload
- **Dynamic Listening History** — Automatic backend logging with Asia/Colombo timezone
- **Smart Recommendations** — Personalized picks based on top genre + time-of-day curation
- **Interactive Profile Charts** — Genre distribution, 7-day trend, mood correlation, and daily evolution powered by Chart.js
- **Playlist Analytics** — Doughnut chart breakdown of saved song genres on profile
- **Seamless Audio Playback** — Dedicated full-screen player with timeline, smart next, and auto-advance
- **Fallback Audio Streaming** — Automatic fallback to a reliable MP3 stream when local files are missing
- **Dark Theme UI** — Premium dark design with glassmorphism, responsive grid, and consistent sidebar navigation

## Tech Stack

| Layer      | Technology |
|------------|------------|
| Backend    | Python, Flask, Werkzeug |
| Database   | SQLite3 |
| Frontend   | HTML5, CSS3, JavaScript (Fetch API, Chart.js) |
| Charting   | Chart.js (CDN) |
| Styling    | Custom CSS with CSS variables, Google Fonts (Inter) |
| Timezone   | pytz (Asia/Colombo) |

## Installation & Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-username/melodyhub.git
   cd melodyhub
   ```

2. **Create a virtual environment (recommended)**
   ```bash
   python -m venv venv
   source venv/bin/activate    # Linux/macOS
   venv\Scripts\activate       # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install flask werkzeug python-dotenv pytz matplotlib
   ```

4. **Set up environment variables**
   
   Create a `.env` file in the project root:
   ```
   SECRET_KEY=your-secret-key-here
   FLASK_ENV=development
   ```

5. **Run the application**
   ```bash
   python app.py
   ```

6. **Open in browser**
   
   Navigate to `http://127.0.0.1:5000`

   Default test accounts (created automatically on first run):
   - Username: `admin` / Password: `1234`
   - Username: `test` / Password: `1234`

## Project Structure

```
melodyhub/
├── app.py                  # Flask application (routes, auth, DB logic)
├── music.db                # SQLite database (auto-created)
├── .env                    # Environment variables
├── requirements.txt        # Python dependencies
├── static/
│   ├── style.css           # Full dark theme stylesheet
│   ├── images/             # Album art, logos, chart exports
│   └── songs/              # MP3 audio files
└── templates/
    ├── base.html           # Skeleton layout
    ├── base_app.html       # Sidebar layout (extended by app pages)
    ├── login.html          # Authentication
    ├── register.html
    ├── index.html          # Home page (hero, recommendations, grids)
    ├── profile.html        # Profile dashboard (charts + analytics)
    ├── playlist.html       # User playlist view
    ├── history.html        # Listening history
    └── player.html         # Full-screen audio player
```

## License

MIT
