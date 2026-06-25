import os
import sqlite3
import traceback
from datetime import datetime, date, timedelta
import pytz
from flask import Flask, render_template, request, redirect, session, jsonify, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24).hex())

import urllib.parse
@app.template_filter('urlencode')
def urlencode_filter(s):
    return urllib.parse.quote(s, safe='')

# Database connection function
def get_db_connection():
    conn = sqlite3.connect('music.db')
    conn.row_factory = sqlite3.Row # This allows us to use column names directly
    return conn

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', 
                            (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect('/')
        else:
            return render_template('login.html', error="Invalid username or password. Please try again.")
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        try:
            # Insert the user into the database
            hashed = generate_password_hash(password)
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed))
            conn.commit()
            conn.close()
            return redirect('/login') # Redirect to login page after successful registration
        except sqlite3.IntegrityError:
            return render_template('register.html', error="This username is already taken. Please choose another.")
            
    return render_template('register.html')


# Home page
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect('/login')
    
    conn = get_db_connection()
    user_id = session['user_id']
    
    # Get default playlist song IDs for persistent +/✓ state
    default_playlist = conn.execute(
        'SELECT id FROM playlists_new WHERE user_id = ? ORDER BY created_at LIMIT 1',
        (user_id,)
    ).fetchone()
    if not default_playlist:
        conn.execute('INSERT INTO playlists_new (user_id, name) VALUES (?, ?)',
                     (user_id, 'My Playlist'))
        conn.commit()
        default_playlist = conn.execute(
            'SELECT id FROM playlists_new WHERE user_id = ? ORDER BY created_at LIMIT 1',
            (user_id,)
        ).fetchone()
    playlist_song_ids = {r['song_id'] for r in conn.execute(
        'SELECT song_id FROM playlist_songs WHERE playlist_id = ?',
        (default_playlist['id'],)
    ).fetchall()}

    def mark_playlist(songs_iter):
        return [{**dict(s), 'is_in_playlist': s['id'] in playlist_song_ids} for s in songs_iter]
    
    # Get history counts per genre
    genre_counts = conn.execute('''
        SELECT s.genre, COUNT(*) as cnt
        FROM history h JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY s.genre
    ''', (user_id,)).fetchall()
    
    # Get disliked song counts per genre
    dislike_counts = conn.execute('''
        SELECT s.genre, COUNT(*) as cnt
        FROM user_likes ul JOIN songs s ON ul.song_id = s.id
        WHERE ul.user_id = ? AND ul.liked = 0
        GROUP BY s.genre
    ''', (user_id,)).fetchall()
    
    hist_dict = {row['genre']: row['cnt'] for row in genre_counts}
    dislike_dict = {row['genre']: row['cnt'] for row in dislike_counts}
    
    all_genres = set(hist_dict.keys()) | set(dislike_dict.keys())
    net_scores = {}
    for genre in all_genres:
        net = hist_dict.get(genre, 0) - dislike_dict.get(genre, 0)
        net_scores[genre] = net if net > 0 else 0
    
    total_net = sum(net_scores.values())
    
    songs = []
    personalized = False
    
    if total_net > 0:
        import random
        target = random.randint(20, 25)
        for genre, net in sorted(net_scores.items(), key=lambda x: -x[1]):
            if net <= 0:
                continue
            pct = net / total_net
            count = max(1, round(pct * target))
            genre_songs = conn.execute('''
                SELECT * FROM songs
                WHERE genre = ? AND NOT EXISTS (
                    SELECT 1 FROM user_likes WHERE user_id = ? AND liked = 0 AND song_id = songs.id
                )
                ORDER BY RANDOM() LIMIT ?
            ''', (genre, user_id, count)).fetchall()
            songs.extend(genre_songs)
        
        random.shuffle(songs)
        songs = songs[:target]
        personalized = True
    else:
        songs = conn.execute('SELECT * FROM songs ORDER BY RANDOM() LIMIT 10').fetchall()
        personalized = False
    
    # Time-of-Day recommendations (also exclude disliked)
    lk_tz = pytz.timezone('Asia/Colombo')
    hour = datetime.now(lk_tz).hour
    time_songs = []
    time_title = ''
    if 5 <= hour < 12:
        time_songs = conn.execute(
            'SELECT * FROM songs WHERE energy > 0.6 AND NOT EXISTS (SELECT 1 FROM user_likes WHERE user_id = ? AND liked = 0 AND song_id = songs.id) ORDER BY RANDOM() LIMIT 5',
            (user_id,)
        ).fetchall()
        time_title = '☀️ Good Morning! Here\'s your energy boost'
    elif hour >= 18:
        time_songs = conn.execute(
            'SELECT * FROM songs WHERE energy < 0.4 AND NOT EXISTS (SELECT 1 FROM user_likes WHERE user_id = ? AND liked = 0 AND song_id = songs.id) ORDER BY RANDOM() LIMIT 5',
            (user_id,)
        ).fetchall()
        time_title = '🌙 Unwind for the Evening'
    
    # Get recently liked songs (up to 10)
    liked_songs = conn.execute('''
        SELECT s.*, ul.created_at
        FROM user_likes ul JOIN songs s ON ul.song_id = s.id
        WHERE ul.user_id = ? AND ul.liked = 1
        ORDER BY ul.created_at DESC LIMIT 10
    ''', (user_id,)).fetchall()

    # Recommended artists — top from history, fall back to random
    recommended_artists = conn.execute('''
        SELECT s.artist, COUNT(*) as cnt
        FROM history h JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY s.artist ORDER BY cnt DESC LIMIT 8
    ''', (user_id,)).fetchall()
    if len(recommended_artists) < 8:
        existing = {r['artist'] for r in recommended_artists}
        needed = 8 - len(recommended_artists)
        fallback = conn.execute(
            'SELECT DISTINCT artist FROM songs WHERE artist NOT IN ({}) ORDER BY RANDOM() LIMIT ?'.format(
                ','.join('?' for _ in existing) if existing else 'NULL'
            ),
            tuple(existing) + (needed,) if existing else (needed,)
        ).fetchall()
        recommended_artists.extend(fallback)
        # Shuffle to mix history picks with fallback
        import random
        random.shuffle(recommended_artists)

    # Mood Vibe Match — energy-based recommendations
    avg_energy = conn.execute('''
        SELECT AVG(s.energy) as avg_energy FROM history h JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
    ''', (user_id,)).fetchone()['avg_energy']

    if avg_energy is not None:
        lo = max(0.0, avg_energy - 0.10)
        hi = min(1.0, avg_energy + 0.10)
        import random
        # Get up to 30 candidates within range, then pick 10 at random
        candidates = conn.execute('''
            SELECT * FROM songs WHERE energy BETWEEN ? AND ? AND NOT EXISTS (
                SELECT 1 FROM user_likes WHERE user_id = ? AND liked = 0 AND song_id = songs.id
            )
        ''', (lo, hi, user_id)).fetchall()
        random.shuffle(candidates)
        energy_recommended_songs = candidates[:10]
    else:
        # No history — fallback to chill/medium energy (0.4–0.6)
        energy_recommended_songs = conn.execute(
            'SELECT * FROM songs WHERE energy BETWEEN 0.4 AND 0.6 ORDER BY RANDOM() LIMIT 8'
        ).fetchall()

    conn.close()
    return render_template('index.html', songs=mark_playlist(songs), personalized=personalized,
                           time_songs=mark_playlist(time_songs), time_title=time_title,
                           liked_songs=mark_playlist(liked_songs),
                           recommended_artists=recommended_artists,
                           energy_recommended_songs=mark_playlist(energy_recommended_songs),
                           is_filtered=False)

@app.route('/recommend', methods=['GET'])
def recommend():
    genre = request.args.get('genre', 'all')
    mood = request.args.get('mood', 'all')
    
    conn = get_db_connection()
    user_id = session.get('user_id')
    
    exclude_disliked = "AND NOT EXISTS (SELECT 1 FROM user_likes WHERE user_id = ? AND liked = 0 AND song_id = songs.id)"
    
    conditions = []
    params = []
    
    if genre != 'all':
        conditions.append("genre = ?")
        params.append(genre)
    
    if mood == 'high':
        conditions.append("energy >= 0.5")
    elif mood == 'low':
        conditions.append("energy < 0.5")
    
    order = "ORDER BY RANDOM()"
    if mood == 'high':
        order = "ORDER BY energy DESC"
    elif mood == 'low':
        order = "ORDER BY energy ASC"
    
    where = " AND ".join(conditions) if conditions else "1=1"
    query = f"SELECT * FROM songs WHERE {where} {exclude_disliked} {order}"
    
    if genre != 'all' or mood != 'all':
        if user_id:
            songs = conn.execute(query, params + [user_id]).fetchall()
        else:
            songs = conn.execute(query.replace(exclude_disliked, ''), params).fetchall()
    else:
        songs = conn.execute(
            "SELECT * FROM songs ORDER BY RANDOM()"
        ).fetchall()
        
    conn.close()
    
    return render_template('index.html', songs=songs, is_filtered=True)

@app.route('/search_suggestions')
def search_suggestions():
    query = request.args.get('query', '').strip()
    if len(query) < 1:
        return jsonify({'suggestions': []})
    conn = get_db_connection()
    songs = conn.execute(
        "SELECT id, title, artist, genre, energy FROM songs WHERE title LIKE ? OR artist LIKE ? LIMIT 8",
        ('%' + query + '%', '%' + query + '%')
    ).fetchall()
    conn.close()
    # Deduplicate by (title, artist) in case of duplicate DB rows
    seen = set()
    deduped = []
    for s in songs:
        key = (s['title'].lower(), s['artist'].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    results = []
    for s in deduped:
        mood = 'happy' if s['energy'] >= 0.5 else 'relax'
        image_url = ''
        if s['genre'] in ('Pop', 'Rock', 'Classical'):
            image_url = url_for('static', filename='images/' + s['genre'] + ' ' + mood + '.jpg')
        results.append({
            'id': s['id'],
            'title': s['title'],
            'artist': s['artist'],
            'genre': s['genre'],
            'image_url': image_url
        })
    return jsonify({'suggestions': results})

@app.route('/search', methods=['GET'])
def search_song():
    query = request.args.get('query')
    
    conn = get_db_connection()
    # Search for songs whose title matches the query (LIKE operator)
    songs = conn.execute("SELECT * FROM songs WHERE title LIKE ?", ('%' + query + '%',)).fetchall()
    conn.close()
    
    return render_template('index.html', songs=songs)

@app.route('/history')
def view_history():
    if 'user_id' not in session:
        return redirect('/login')
    
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    conn = get_db_connection()
    
    total = conn.execute('SELECT COUNT(*) FROM history WHERE user_id = ?',
                         (session['user_id'],)).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    
    history = conn.execute('''
        SELECT history.id, history.song_id, songs.title, songs.artist, history.played_at 
        FROM history 
        JOIN songs ON history.song_id = songs.id 
        WHERE history.user_id = ? 
        ORDER BY history.played_at DESC LIMIT ? OFFSET ?
    ''', (session['user_id'], per_page, offset)).fetchall()
    conn.close()
    
    return render_template('history.html', history=history, page=page, total_pages=total_pages)

@app.route('/delete_history/<int:history_id>', methods=['POST'])
def delete_history(history_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    conn = get_db_connection()
    conn.execute('DELETE FROM history WHERE id = ? AND user_id = ?',
                 (history_id, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# 3. Endpoint to save history in the background when play button is pressed
@app.route('/add_to_history', methods=['POST'])
def add_to_history():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    data = request.get_json()
    song_id = data.get('song_id')
    
    try:
        lk_tz = pytz.timezone('Asia/Colombo')
        naive_local_time = datetime.now(lk_tz).replace(tzinfo=None)
        
        conn = get_db_connection()
        conn.execute('INSERT INTO history (user_id, song_id, played_at) VALUES (?, ?, ?)',
                     (session['user_id'], song_id, naive_local_time))
        conn.commit()
        conn.close()
        
        print(f"--> SUCCESS: Song {song_id} logged into history at {naive_local_time}")
        
    except Exception as db_error:
        print("--> DATABASE INSERTION FAILED! ERROR DETAILS BELOW:")
        print(str(db_error))
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(db_error)}), 500
    
    return jsonify({"status": "success", "message": "History saved"})

@app.route('/smart_next', methods=['POST'])
def smart_next():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.get_json()
    current_song_id = data.get('song_id')
    playlist_id = data.get('playlist_id')

    conn = get_db_connection()

    if playlist_id:
        # Fetch playlist song IDs in exact sequence (matching playlist view order)
        pl_rows = conn.execute('''
            SELECT ps.song_id FROM playlist_songs ps
            WHERE ps.playlist_id = ?
            ORDER BY ps.id DESC
        ''', (playlist_id,)).fetchall()
        song_ids = [r['song_id'] for r in pl_rows]
        next_song = None
        if current_song_id in song_ids:
            current_index = song_ids.index(current_song_id)
            next_ids = song_ids[current_index + 1:]
            if next_ids:
                next_song_id = next_ids[0]
            else:
                next_song_id = song_ids[0]  # wrap to first
            if next_song_id:
                next_song = conn.execute('SELECT * FROM songs WHERE id = ?', (next_song_id,)).fetchone()
        if not next_song:
            # Fallback: playlist empty or song not found — get random by genre
            current = conn.execute('SELECT genre FROM songs WHERE id = ?', (current_song_id,)).fetchone()
            if current:
                next_song = conn.execute(
                    'SELECT * FROM songs WHERE genre = ? AND id != ? ORDER BY RANDOM() LIMIT 1',
                    (current['genre'], current_song_id)
                ).fetchone()
            if not next_song:
                next_song = conn.execute(
                    'SELECT * FROM songs ORDER BY RANDOM() LIMIT 1'
                ).fetchone()
    else:
        # Get genre of the current song
        current = conn.execute('SELECT genre FROM songs WHERE id = ?', (current_song_id,)).fetchone()
        if current:
            next_song = conn.execute(
                'SELECT * FROM songs WHERE genre = ? AND id != ? ORDER BY RANDOM() LIMIT 1',
                (current['genre'], current_song_id)
            ).fetchone()
        else:
            next_song = conn.execute(
                'SELECT * FROM songs ORDER BY RANDOM() LIMIT 1'
            ).fetchone()

    conn.close()

    if next_song:
        return jsonify({
            "status": "success",
            "song": {
                "id": next_song['id'],
                "title": next_song['title'],
                "artist": next_song['artist'],
                "genre": next_song['genre'],
                "energy": next_song['energy'],
                "file_path": url_for('static', filename='songs/' + next_song['file_path'])
            }
        })
    return jsonify({"status": "error", "message": "No songs found"}), 404

@app.route('/profile')
def profile_analysis():
    if 'user_id' not in session:
        return redirect('/login')
        
    conn = get_db_connection()
    user_id = session['user_id']
    
    # 1. Get play count for each genre (for Pie Chart)
    genre_stats = conn.execute('''
        SELECT songs.genre, COUNT(history.id) as play_count 
        FROM history 
        JOIN songs ON history.song_id = songs.id 
        WHERE history.user_id = ? 
        GROUP BY songs.genre
    ''', (user_id,)).fetchall()
    
    # Split data into two lists for JavaScript
    chart_labels = [row['genre'] for row in genre_stats]
    chart_data = [row['play_count'] for row in genre_stats]
    
    # 2. User's most listened genre (for Top Card)
    top_genre = conn.execute('''
        SELECT songs.genre, COUNT(history.id) as play_count 
        FROM history 
        JOIN songs ON history.song_id = songs.id 
        WHERE history.user_id = ? 
        GROUP BY songs.genre 
        ORDER BY play_count DESC LIMIT 1
    ''', (user_id,)).fetchone()
    
    # 3. User's dominant mood type
    top_mood = conn.execute('''
        SELECT 
            CASE WHEN songs.energy > 0.6 THEN 'Happy / High Energy' ELSE 'Relax / Low Energy' END as mood_type,
            COUNT(history.id) as mood_count
        FROM history 
        JOIN songs ON history.song_id = songs.id 
        WHERE history.user_id = ? 
        GROUP BY mood_type 
        ORDER BY mood_count DESC LIMIT 1
    ''', (user_id,)).fetchone()
    
    # 4. Total songs played count
    total_played = conn.execute('SELECT COUNT(*) FROM history WHERE user_id = ?', (user_id,)).fetchone()[0]
    
    # 5. Average listening energy
    avg_row = conn.execute('''
        SELECT AVG(s.energy) as avg_energy
        FROM history h
        JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
    ''', (user_id,)).fetchone()
    avg_energy = round(avg_row['avg_energy'], 2) if avg_row and avg_row['avg_energy'] else 0
    
    # 6. Weekly listening trend (last 7 days)
    lk_tz = pytz.timezone('Asia/Colombo')
    six_days_ago = (datetime.now(lk_tz) - timedelta(days=6)).strftime('%Y-%m-%d')
    trend_rows = conn.execute('''
        SELECT DATE(played_at) as day, COUNT(*) as count
        FROM history
        WHERE user_id = ? AND played_at >= ?
        GROUP BY DATE(played_at)
        ORDER BY day
    ''', (user_id, six_days_ago)).fetchall()
    
    trend_labels = []
    trend_data = []
    counts_by_day = {row['day']: row['count'] for row in trend_rows}
    today = date.today()
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        date_str = d.isoformat()
        trend_labels.append(day_names[d.weekday()])
        trend_data.append(counts_by_day.get(date_str, 0))
    
    # 7. Genre vs Mood correlation (stacked bar)
    corr_rows = conn.execute('''
        SELECT 
            s.genre,
            SUM(CASE WHEN s.energy >= 0.5 THEN 1 ELSE 0 END) as happy_count,
            SUM(CASE WHEN s.energy < 0.5 THEN 1 ELSE 0 END) as chill_count
        FROM history h
        JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY s.genre
        ORDER BY s.genre
    ''', (user_id,)).fetchall()
    corr_genres = [row['genre'] for row in corr_rows]
    corr_happy = [row['happy_count'] for row in corr_rows]
    corr_chill = [row['chill_count'] for row in corr_rows]
    
    # 8. Recently played songs
    recent_rows = conn.execute('''
        SELECT s.title, s.artist, h.played_at
        FROM history h
        JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        ORDER BY h.played_at DESC LIMIT 6
    ''', (user_id,)).fetchall()
    
    # 9. Daily Mood & Genre Evolution (average energy + top genre per day)
    daily_energy_rows = conn.execute('''
        SELECT DATE(h.played_at) as day, ROUND(AVG(s.energy), 2) as avg_energy
        FROM history h
        JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY DATE(h.played_at)
        ORDER BY day
    ''', (user_id,)).fetchall()
    
    daily_genre_rows = conn.execute('''
        SELECT DATE(h.played_at) as day, s.genre, COUNT(*) as cnt
        FROM history h
        JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY day, s.genre
        ORDER BY day, cnt DESC
    ''', (user_id,)).fetchall()
    
    daily_dates = [row['day'] for row in daily_energy_rows]
    daily_energies = [row['avg_energy'] for row in daily_energy_rows]
    from collections import OrderedDict
    top_genre_by_day = OrderedDict()
    for row in daily_genre_rows:
        if row['day'] not in top_genre_by_day:
            top_genre_by_day[row['day']] = row['genre']
    daily_genres = [top_genre_by_day.get(d, '—') for d in daily_dates]
    
    # Playlist genre distribution (across all user's playlists)
    playlist_genre_rows = conn.execute('''
        SELECT s.genre, COUNT(*) as cnt
        FROM playlist_songs ps
        JOIN playlists_new p ON ps.playlist_id = p.id
        JOIN songs s ON ps.song_id = s.id
        WHERE p.user_id = ?
        GROUP BY s.genre
        ORDER BY cnt DESC
    ''', (user_id,)).fetchall()
    pl_genres = [row['genre'] for row in playlist_genre_rows]
    pl_counts = [row['cnt'] for row in playlist_genre_rows]
    
    # 10. Combined like/dislike per genre (single unified dataset)
    ld_rows = conn.execute('''
        SELECT s.genre,
               SUM(CASE WHEN ul.liked = 1 THEN 1 ELSE 0 END) as likes,
               SUM(CASE WHEN ul.liked = 0 THEN 1 ELSE 0 END) as dislikes
        FROM user_likes ul
        JOIN songs s ON ul.song_id = s.id
        WHERE ul.user_id = ?
        GROUP BY s.genre
        ORDER BY (likes + dislikes) DESC
    ''', (user_id,)).fetchall()
    ld_genres = [row['genre'] for row in ld_rows]
    ld_likes = [row['likes'] for row in ld_rows]
    ld_dislikes = [row['dislikes'] for row in ld_rows]
    
    # 11. Top liked songs
    like_song_rows = conn.execute('''
        SELECT s.title, s.artist
        FROM user_likes ul
        JOIN songs s ON ul.song_id = s.id
        WHERE ul.user_id = ? AND ul.liked = 1
        ORDER BY ul.created_at DESC LIMIT 5
    ''', (user_id,)).fetchall()
    like_songs = [dict(row) for row in like_song_rows]
    
    # 12. Top disliked songs
    dislike_song_rows = conn.execute('''
        SELECT s.title, s.artist
        FROM user_likes ul
        JOIN songs s ON ul.song_id = s.id
        WHERE ul.user_id = ? AND ul.liked = 0
        ORDER BY ul.created_at DESC LIMIT 5
    ''', (user_id,)).fetchall()
    dislike_songs = [dict(row) for row in dislike_song_rows]

    # 13. Top 3 most played artists
    top_artists = conn.execute('''
        SELECT s.artist, COUNT(*) as cnt
        FROM history h JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY s.artist ORDER BY cnt DESC LIMIT 3
    ''', (user_id,)).fetchall()

    # 14. Top 3 most played songs
    top_songs = conn.execute('''
        SELECT s.id, s.title, s.artist, s.genre, s.energy, COUNT(*) as cnt
        FROM history h JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY h.song_id ORDER BY cnt DESC LIMIT 3
    ''', (user_id,)).fetchall()
    
    conn.close()
    
    return render_template('profile.html', 
                           top_artists=top_artists,
                           top_songs=top_songs,
                           top_genre=top_genre, 
                           top_mood=top_mood, 
                           total_played=total_played,
                           avg_energy=avg_energy,
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           trend_labels=trend_labels,
                           trend_data=trend_data,
                           corr_genres=corr_genres,
                           corr_happy=corr_happy,
                           corr_chill=corr_chill,
                           recent=recent_rows,
                            daily_dates=daily_dates,
                            daily_energies=daily_energies,
                            daily_genres=daily_genres,
                            pl_genres=pl_genres,
                            pl_counts=pl_counts,
                             ld_genres=ld_genres,
                             ld_likes=ld_likes,
                             ld_dislikes=ld_dislikes,
                             like_songs=like_songs,
                             dislike_songs=dislike_songs)

@app.route('/api/profile-analytics')
def profile_analytics_json():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    conn = get_db_connection()
    user_id = session['user_id']

    genre_stats = conn.execute('SELECT songs.genre, COUNT(history.id) as play_count FROM history JOIN songs ON history.song_id = songs.id WHERE history.user_id = ? GROUP BY songs.genre', (user_id,)).fetchall()
    chart_labels = [row['genre'] for row in genre_stats]
    chart_data = [row['play_count'] for row in genre_stats]

    trend_rows = conn.execute("SELECT DATE(played_at) as day, COUNT(*) as count FROM history WHERE user_id = ? AND played_at >= ? GROUP BY DATE(played_at) ORDER BY day", (user_id, (datetime.now(pytz.timezone('Asia/Colombo')) - timedelta(days=6)).strftime('%Y-%m-%d'))).fetchall()
    counts_by_day = {row['day']: row['count'] for row in trend_rows}
    today = date.today()
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    trend_labels = []
    trend_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        trend_labels.append(day_names[d.weekday()])
        trend_data.append(counts_by_day.get(d.isoformat(), 0))

    corr_rows = conn.execute("SELECT s.genre, SUM(CASE WHEN s.energy >= 0.5 THEN 1 ELSE 0 END) as happy_count, SUM(CASE WHEN s.energy < 0.5 THEN 1 ELSE 0 END) as chill_count FROM history h JOIN songs s ON h.song_id = s.id WHERE h.user_id = ? GROUP BY s.genre ORDER BY s.genre", (user_id,)).fetchall()
    corr_genres = [row['genre'] for row in corr_rows]
    corr_happy = [row['happy_count'] for row in corr_rows]
    corr_chill = [row['chill_count'] for row in corr_rows]

    daily_energy_rows = conn.execute("SELECT DATE(h.played_at) as day, ROUND(AVG(s.energy), 2) as avg_energy FROM history h JOIN songs s ON h.song_id = s.id WHERE h.user_id = ? GROUP BY DATE(h.played_at) ORDER BY day", (user_id,)).fetchall()
    daily_genre_rows = conn.execute("SELECT DATE(h.played_at) as day, s.genre, COUNT(*) as cnt FROM history h JOIN songs s ON h.song_id = s.id WHERE h.user_id = ? GROUP BY day, s.genre ORDER BY day, cnt DESC", (user_id,)).fetchall()
    daily_dates = [row['day'] for row in daily_energy_rows]
    daily_energies = [row['avg_energy'] for row in daily_energy_rows]
    from collections import OrderedDict
    top_genre_by_day = OrderedDict()
    for row in daily_genre_rows:
        if row['day'] not in top_genre_by_day:
            top_genre_by_day[row['day']] = row['genre']
    daily_genres = [top_genre_by_day.get(d, '—') for d in daily_dates]

    pl_rows = conn.execute("SELECT s.genre, COUNT(*) as cnt FROM playlist_songs ps JOIN playlists_new p ON ps.playlist_id = p.id JOIN songs s ON ps.song_id = s.id WHERE p.user_id = ? GROUP BY s.genre ORDER BY cnt DESC", (user_id,)).fetchall()
    pl_genres = [row['genre'] for row in pl_rows]
    pl_counts = [row['cnt'] for row in pl_rows]

    ld_rows = conn.execute("SELECT s.genre, SUM(CASE WHEN ul.liked = 1 THEN 1 ELSE 0 END) as likes, SUM(CASE WHEN ul.liked = 0 THEN 1 ELSE 0 END) as dislikes FROM user_likes ul JOIN songs s ON ul.song_id = s.id WHERE ul.user_id = ? GROUP BY s.genre ORDER BY (likes + dislikes) DESC", (user_id,)).fetchall()
    ld_genres = [row['genre'] for row in ld_rows]
    ld_likes = [row['likes'] for row in ld_rows]
    ld_dislikes = [row['dislikes'] for row in ld_rows]

    conn.close()
    return jsonify({
        'genres': chart_labels,
        'counts': chart_data,
        'trend': {'labels': trend_labels, 'data': trend_data},
        'correlation': {'genres': corr_genres, 'happy': corr_happy, 'chill': corr_chill},
        'daily': {'dates': daily_dates, 'energies': daily_energies, 'genres': daily_genres},
        'likeDislike': {'genres': ld_genres, 'likes': ld_likes, 'dislikes': ld_dislikes},
        'playlistGenre': {'genres': pl_genres, 'counts': pl_counts},
    })

# ===== MULTI-PLAYLIST ROUTES =====

# Legacy compat: add song to default (first) playlist
@app.route('/add_to_playlist/<int:song_id>', methods=['POST'])
def legacy_add_to_playlist(song_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    conn = get_db_connection()
    default = conn.execute('SELECT id FROM playlists_new WHERE user_id = ? ORDER BY created_at LIMIT 1',
                           (session['user_id'],)).fetchone()
    if not default:
        conn.execute('INSERT INTO playlists_new (user_id, name) VALUES (?, ?)',
                     (session['user_id'], 'My Playlist'))
        conn.commit()
        default = conn.execute('SELECT id FROM playlists_new WHERE user_id = ? ORDER BY created_at LIMIT 1',
                               (session['user_id'],)).fetchone()
    existing = conn.execute('SELECT 1 FROM playlist_songs WHERE playlist_id = ? AND song_id = ?',
                            (default['id'], song_id)).fetchone()
    if existing:
        conn.close()
        return jsonify({'status': 'error', 'message': 'This song is already in this playlist!'}), 400
    conn.execute('INSERT INTO playlist_songs (playlist_id, song_id) VALUES (?, ?)',
                 (default['id'], song_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Song added to playlist'})

@app.route('/create_playlist', methods=['POST'])
def create_playlist():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': 'Playlist name required'}), 400
    conn = get_db_connection()
    c = conn.execute('INSERT INTO playlists_new (user_id, name) VALUES (?, ?)',
                     (session['user_id'], name))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    # Return JSON for AJAX requests, redirect for form submissions
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.best == 'application/json':
        return jsonify({'success': True, 'playlist_id': new_id, 'name': name})
    return redirect('/playlist')

@app.route('/delete_playlist/<int:playlist_id>', methods=['POST'])
def delete_playlist(playlist_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    pl = conn.execute('SELECT * FROM playlists_new WHERE id = ? AND user_id = ?',
                      (playlist_id, session['user_id'])).fetchone()
    if pl:
        conn.execute('DELETE FROM playlist_songs WHERE playlist_id = ?', (playlist_id,))
        conn.execute('DELETE FROM playlists_new WHERE id = ?', (playlist_id,))
        conn.commit()
    conn.close()
    return redirect('/playlist')

@app.route('/add_to_playlist/<int:playlist_id>/<int:song_id>', methods=['POST'])
def add_song_to_playlist(playlist_id, song_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    conn = get_db_connection()
    pl = conn.execute('SELECT * FROM playlists_new WHERE id = ? AND user_id = ?',
                      (playlist_id, session['user_id'])).fetchone()
    if not pl:
        conn.close()
        return jsonify({'success': False, 'message': 'Playlist not found'}), 404
    existing = conn.execute('SELECT 1 FROM playlist_songs WHERE playlist_id = ? AND song_id = ?',
                            (playlist_id, song_id)).fetchone()
    if existing:
        conn.close()
        return jsonify({'status': 'error', 'message': 'This song is already in this playlist!'}), 400
    conn.execute('INSERT INTO playlist_songs (playlist_id, song_id) VALUES (?, ?)',
                 (playlist_id, song_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Song added to playlist'})

@app.route('/remove_from_playlist/<int:playlist_id>/<int:song_id>', methods=['POST'])
def remove_song_from_playlist(playlist_id, song_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    pl = conn.execute('SELECT * FROM playlists_new WHERE id = ? AND user_id = ?',
                      (playlist_id, session['user_id'])).fetchone()
    if pl:
        conn.execute('DELETE FROM playlist_songs WHERE playlist_id = ? AND song_id = ?',
                     (playlist_id, song_id))
        conn.commit()
    conn.close()
    return redirect('/playlist/' + str(playlist_id))

# View all playlists (index)
@app.route('/playlist')
def view_playlists():
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    playlists = conn.execute('''
        SELECT p.*, (SELECT COUNT(*) FROM playlist_songs WHERE playlist_id = p.id) as song_count
        FROM playlists_new p WHERE p.user_id = ?
        ORDER BY p.created_at DESC
    ''', (session['user_id'],)).fetchall()
    conn.close()
    return render_template('playlist.html', playlists=playlists, active_playlist=None, songs=[])

# API to list user's playlists (for playlist picker)
@app.route('/get_playlists')
def get_playlists_api():
    if 'user_id' not in session:
        return jsonify({'playlists': []})
    conn = get_db_connection()
    playlists = conn.execute('''
        SELECT p.id, p.name,
               (SELECT COUNT(*) FROM playlist_songs WHERE playlist_id = p.id) as song_count
        FROM playlists_new p WHERE p.user_id = ?
        ORDER BY p.created_at DESC
    ''', (session['user_id'],)).fetchall()
    conn.close()
    return jsonify({'playlists': [dict(r) for r in playlists]})

# View a specific playlist
@app.route('/playlist/<int:playlist_id>')
def view_specific_playlist(playlist_id):
    if 'user_id' not in session:
        return redirect('/login')
    conn = get_db_connection()
    playlists = conn.execute('''
        SELECT p.*, (SELECT COUNT(*) FROM playlist_songs WHERE playlist_id = p.id) as song_count
        FROM playlists_new p WHERE p.user_id = ?
        ORDER BY p.created_at DESC
    ''', (session['user_id'],)).fetchall()
    active = conn.execute('SELECT * FROM playlists_new WHERE id = ? AND user_id = ?',
                          (playlist_id, session['user_id'])).fetchone()
    songs = []
    if active:
        songs = conn.execute('''
            SELECT s.* FROM playlist_songs ps
            JOIN songs s ON ps.song_id = s.id
            WHERE ps.playlist_id = ?
            ORDER BY ps.added_at DESC
        ''', (playlist_id,)).fetchall()
    conn.close()
    return render_template('playlist.html', playlists=playlists,
                           active_playlist=active, songs=songs)

@app.route('/like/<int:song_id>', methods=['POST'])
def like_song(song_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    liked = data.get('liked', True)
    liked_int = 1 if liked else 0
    
    conn = get_db_connection()
    existing = conn.execute('SELECT * FROM user_likes WHERE user_id = ? AND song_id = ?',
                            (session['user_id'], song_id)).fetchone()
    if existing:
        conn.execute('UPDATE user_likes SET liked = ? WHERE user_id = ? AND song_id = ?',
                     (liked_int, session['user_id'], song_id))
    else:
        conn.execute('INSERT INTO user_likes (user_id, song_id, liked) VALUES (?, ?, ?)',
                     (session['user_id'], song_id, liked_int))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'liked': liked})

@app.route('/api/like_status/<int:song_id>')
def like_status(song_id):
    if 'user_id' not in session:
        return jsonify({'liked': None})
    conn = get_db_connection()
    row = conn.execute('SELECT liked FROM user_likes WHERE user_id = ? AND song_id = ?',
                       (session['user_id'], song_id)).fetchone()
    conn.close()
    return jsonify({'liked': row['liked'] if row else None})

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if 'user_id' not in session:
        return redirect('/login')
    
    conn = get_db_connection()
    user = conn.execute('SELECT id, username FROM users WHERE id = ?',
                        (session['user_id'],)).fetchone()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update_username':
            new_username = request.form.get('username', '').strip()
            if not new_username:
                conn.close()
                return render_template('settings.html', user=user, error='Username cannot be empty.')
            existing = conn.execute('SELECT id FROM users WHERE username = ? AND id != ?',
                                    (new_username, session['user_id'])).fetchone()
            if existing:
                conn.close()
                return render_template('settings.html', user=user, error='Username already taken.')
            conn.execute('UPDATE users SET username = ? WHERE id = ?',
                         (new_username, session['user_id']))
            conn.commit()
            session['username'] = new_username
            conn.close()
            return render_template('settings.html', user={'id': session['user_id'], 'username': new_username}, success='Username updated.')
        
        elif action == 'change_password':
            current_pw = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            confirm_pw = request.form.get('confirm_password', '')
            if not current_pw or not new_pw or not confirm_pw:
                conn.close()
                return render_template('settings.html', user=user, error='All password fields are required.')
            if new_pw != confirm_pw:
                conn.close()
                return render_template('settings.html', user=user, error='New passwords do not match.')
            stored = conn.execute('SELECT password FROM users WHERE id = ?',
                                  (session['user_id'],)).fetchone()
            if not stored or not check_password_hash(stored['password'], current_pw):
                conn.close()
                return render_template('settings.html', user=user, error='Current password is incorrect.')
            hashed = generate_password_hash(new_pw)
            conn.execute('UPDATE users SET password = ? WHERE id = ?',
                         (hashed, session['user_id']))
            conn.commit()
            conn.close()
            return render_template('settings.html', user=user, success='Password changed.')
        
        elif action == 'delete_account':
            uid = session['user_id']
            conn.execute('DELETE FROM user_likes WHERE user_id = ?', (uid,))
            conn.execute('DELETE FROM playlist_songs WHERE playlist_id IN (SELECT id FROM playlists_new WHERE user_id = ?)', (uid,))
            conn.execute('DELETE FROM history WHERE user_id = ?', (uid,))
            conn.execute('DELETE FROM playlists_new WHERE user_id = ?', (uid,))
            conn.execute('DELETE FROM users WHERE id = ?', (uid,))
            conn.commit()
            conn.close()
            session.clear()
            return redirect('/login?deleted=1')
    
    conn.close()
    return render_template('settings.html', user=user)

@app.route('/clear_history', methods=['POST'])
def clear_history():
    if 'user_id' not in session:
        return redirect('/login')
        
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Delete all history records for this user from the database
    conn.execute('DELETE FROM history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    return redirect('/history') # Redirect back to the history page after clearing

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/player/<int:song_id>')
def player(song_id):
    if 'user_id' not in session:
        return redirect('/login')
    
    playlist_id = request.args.get('playlist_id', type=int)
    
    conn = get_db_connection()
    song = conn.execute('SELECT * FROM songs WHERE id = ?', (song_id,)).fetchone()
    if not song:
        conn.close()
        return redirect('/')
    
    # Ensure a valid audio path — fall back to silent default if missing
    song = dict(song)
    if not song.get('file_path') or song['file_path'].strip() == '':
        song['file_path'] = 'default.mp3'
    
    recommendations = []
    playlist_name = "Recommended Suggestions"
    if playlist_id:
        # Get playlist name
        pl_info = conn.execute('SELECT name FROM playlists_new WHERE id = ?',
                               (playlist_id,)).fetchone()
        if pl_info:
            playlist_name = pl_info['name']
        # 1. Get all playlist-song records in exact sequence (matching playlist view order)
        pl_rows = conn.execute('''
            SELECT ps.song_id FROM playlist_songs ps
            WHERE ps.playlist_id = ?
            ORDER BY ps.id DESC
        ''', (playlist_id,)).fetchall()
        song_ids = [r['song_id'] for r in pl_rows]

        if song_id in song_ids:
            current_index = song_ids.index(song_id)
            next_song_ids = song_ids[current_index + 1:]

            if next_song_ids:
                placeholders = ','.join('?' for _ in next_song_ids)
                fetched = conn.execute(f'''
                    SELECT * FROM songs WHERE id IN ({placeholders})
                ''', next_song_ids).fetchall()
                # Re-sort to match exact playlist order
                id_map = {s['id']: s for s in fetched}
                recommendations = [id_map[sid] for sid in next_song_ids if sid in id_map]
        # If no remaining songs in playlist, fall back to default recommendations
        if not recommendations:
            recommendations = conn.execute(
                'SELECT * FROM songs WHERE genre = ? AND id != ? ORDER BY RANDOM() LIMIT 6',
                (song['genre'], song_id)
            ).fetchall()
    else:
        recommendations = conn.execute(
            'SELECT * FROM songs WHERE genre = ? AND id != ? ORDER BY RANDOM() LIMIT 6',
            (song['genre'], song_id)
        ).fetchall()
    
    # Backend history logging trigger (fires on page load, independent of audio file)
    try:
        lk_tz = pytz.timezone('Asia/Colombo')
        naive_local_time = datetime.now(lk_tz).replace(tzinfo=None)
        conn.execute('INSERT INTO history (user_id, song_id, played_at) VALUES (?, ?, ?)',
                     (session['user_id'], song_id, naive_local_time))
        conn.commit()
        print(f"--> SUCCESS: Song {song_id} forced into history at {naive_local_time} via Backend Route")
    except Exception as e:
        print("--> Backend history tracking failed:", str(e))
        traceback.print_exc()
    
    conn.close()
    
    return render_template('player.html', song=song, recommendations=recommendations, playlist_id=playlist_id, playlist_name=playlist_name)

@app.route('/api/song/<int:song_id>')
def api_song(song_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db_connection()
    song = conn.execute('SELECT * FROM songs WHERE id = ?', (song_id,)).fetchone()
    conn.close()
    if not song:
        return jsonify({'error': 'Not found'}), 404
    song = dict(song)
    mood = 'happy' if song['energy'] >= 0.5 else 'relax'
    image_url = ''
    if song['genre'] in ('Pop', 'Rock', 'Classical'):
        image_url = url_for('static', filename='images/' + song['genre'] + ' ' + mood + '.jpg')
    return jsonify({
        'id': song['id'],
        'title': song['title'],
        'artist': song['artist'],
        'genre': song['genre'],
        'energy': song['energy'],
        'file_path': url_for('static', filename='songs/' + song['file_path']) if song.get('file_path') else '',
        'image_url': image_url
    })

def init_db():
    conn = sqlite3.connect('music.db')
    c = conn.cursor()
    # Create new multi-playlist tables if they don't exist
    c.execute('''CREATE TABLE IF NOT EXISTS playlists_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS playlist_songs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        playlist_id INTEGER NOT NULL,
        song_id INTEGER NOT NULL,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (playlist_id) REFERENCES playlists_new(id),
        FOREIGN KEY (song_id) REFERENCES songs(id)
    )''')
    # Create user_likes table for like/dislike system
    c.execute('''CREATE TABLE IF NOT EXISTS user_likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        song_id INTEGER NOT NULL,
        liked INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (song_id) REFERENCES songs(id),
        UNIQUE(user_id, song_id)
    )''')
    # Ensure every user has at least one default playlist
    users = c.execute('SELECT id FROM users').fetchall()
    for (uid,) in users:
        existing = c.execute('SELECT id FROM playlists_new WHERE user_id = ?', (uid,)).fetchone()
        if not existing:
            c.execute('INSERT INTO playlists_new (user_id, name) VALUES (?, ?)', (uid, 'My Playlist'))
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    # Create sample user accounts
    conn = sqlite3.connect('music.db')
    cursor = conn.cursor()
    try:
        hashed = generate_password_hash('1234')
        cursor.execute("INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)", ('admin', hashed))
        cursor.execute("INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)", ('test', hashed))
        conn.commit()
        print("Sample users (admin, 1234) created successfully!")
    except Exception as e:
        print("Failed to create sample users:", e)
    
    # ---- Auto-seed sample listening history, likes & dislikes ----
    try:
        # Find the admin user's actual ID
        cursor.execute('SELECT id FROM users WHERE username = ?', ('admin',))
        row = cursor.fetchone()
        if row:
            uid = row[0]
            cursor.execute('SELECT COUNT(*) FROM history WHERE user_id = ?', (uid,))
            hist_count = cursor.fetchone()[0]
            if hist_count == 0:
                from datetime import timedelta
                lk_tz = pytz.timezone('Asia/Colombo')
                now = datetime.now(lk_tz)
                # 14 sample history entries across last 5 days — Pop, Rock, Indie
                sample_songs = [
                    (1, 2, 3, 4, 5, 6),      # Pop songs (IDs 1-6)
                    (29, 30, 31, 32),          # Rock songs (IDs 29-32)
                    (53, 54, 55),              # Indie songs (IDs 53-55)
                ]
                import itertools
                flat_songs = list(itertools.chain(*sample_songs))
                history_rows = []
                for i in range(14):
                    day_offset = i // 3
                    played_at = (now - timedelta(days=day_offset, hours=i % 24)).replace(tzinfo=None)
                    song_id = flat_songs[i % len(flat_songs)]
                    history_rows.append((uid, song_id, played_at))
                cursor.executemany(
                    'INSERT INTO history (user_id, song_id, played_at) VALUES (?, ?, ?)',
                    history_rows
                )
                print(f"--> Seeded {len(history_rows)} history entries for user {uid}")
                
                # 4 likes
                like_songs = [1, 5, 29, 54]
                for sid in like_songs:
                    cursor.execute(
                        'INSERT OR IGNORE INTO user_likes (user_id, song_id, liked) VALUES (?, ?, 1)',
                        (uid, sid)
                    )
                print(f"--> Seeded {len(like_songs)} likes for user {uid}")
                
                # 3 dislikes
                dislike_songs = [3, 31, 55]
                for sid in dislike_songs:
                    cursor.execute(
                        'INSERT OR IGNORE INTO user_likes (user_id, song_id, liked) VALUES (?, ?, 0)',
                        (uid, sid)
                    )
                print(f"--> Seeded {len(dislike_songs)} dislikes for user {uid}")
                
                conn.commit()
                print("--> Auto-seeding complete!")
            else:
                print(f"--> History already has {hist_count} entries — skipping seed")
    except Exception as e:
        print("--> Auto-seed error:", str(e))
        traceback.print_exc()
    finally:
        conn.close()

    app.run(debug=True)

