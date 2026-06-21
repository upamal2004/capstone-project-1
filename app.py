import os
import sqlite3
import traceback
from datetime import datetime, date, timedelta
import pytz
from flask import Flask, render_template, request, redirect, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24).hex())

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
    
    # Find user's top genre from history
    top = conn.execute('''
        SELECT s.genre, COUNT(*) as cnt
        FROM history h
        JOIN songs s ON h.song_id = s.id
        WHERE h.user_id = ?
        GROUP BY s.genre
        ORDER BY cnt DESC LIMIT 1
    ''', (user_id,)).fetchone()
    
    if top:
        songs = conn.execute('SELECT * FROM songs WHERE genre = ? ORDER BY RANDOM() LIMIT 10', (top['genre'],)).fetchall()
        personalized = True
    else:
        songs = conn.execute('SELECT * FROM songs LIMIT 10').fetchall()
        personalized = False
    
    # Time-of-Day recommendations (Sri Lanka time)
    lk_tz = pytz.timezone('Asia/Colombo')
    hour = datetime.now(lk_tz).hour
    time_songs = []
    time_title = ''
    if 5 <= hour < 12:
        time_songs = conn.execute(
            'SELECT * FROM songs WHERE energy > 0.6 ORDER BY RANDOM() LIMIT 5'
        ).fetchall()
        time_title = '☀️ Good Morning! Here\'s your energy boost'
    elif hour >= 18:
        time_songs = conn.execute(
            'SELECT * FROM songs WHERE energy < 0.4 ORDER BY RANDOM() LIMIT 5'
        ).fetchall()
        time_title = '🌙 Unwind for the Evening'
    
    conn.close()
    return render_template('index.html', songs=songs, personalized=personalized,
                           time_songs=time_songs, time_title=time_title)

@app.route('/recommend', methods=['GET'])
def recommend():
    genre = request.args.get('genre')
    mood = request.args.get('mood')
    
    conn = get_db_connection()
    
    if mood == 'high':
        songs = conn.execute(
            "SELECT * FROM songs WHERE genre = ? AND energy >= 0.5 ORDER BY energy DESC",
            (genre,)
        ).fetchall()
    else:
        songs = conn.execute(
            "SELECT * FROM songs WHERE genre = ? AND energy < 0.5 ORDER BY energy ASC",
            (genre,)
        ).fetchall()
        
    conn.close()
    
    return render_template('index.html', songs=songs)

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
    
    conn = get_db_connection()
    # Fetch user's history with song details via JOIN
    history = conn.execute('''
        SELECT songs.title, songs.artist, history.played_at 
        FROM history 
        JOIN songs ON history.song_id = songs.id 
        WHERE history.user_id = ? 
        ORDER BY history.played_at DESC LIMIT 10
    ''', (session['user_id'],)).fetchall()
    conn.close()
    
    return render_template('history.html', history=history)

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

    conn = get_db_connection()
    # Get genre of the current song
    current = conn.execute('SELECT genre FROM songs WHERE id = ?', (current_song_id,)).fetchone()
    if current:
        # Fetch a random song from the same genre, excluding the current song
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
                "file_path": url_for('static', filename='songs/' + next_song['file_path'])
            }
        })
    return jsonify({"status": "error", "message": "No songs found"}), 404
        
    return jsonify({"status": "error", "message": "Invalid song ID"}), 400

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
    
    # Playlist genre distribution
    playlist_genre_rows = conn.execute('''
        SELECT s.genre, COUNT(*) as cnt
        FROM playlists p
        JOIN songs s ON p.song_id = s.id
        WHERE p.user_id = ?
        GROUP BY s.genre
        ORDER BY cnt DESC
    ''', (user_id,)).fetchall()
    pl_genres = [row['genre'] for row in playlist_genre_rows]
    pl_counts = [row['cnt'] for row in playlist_genre_rows]
    
    conn.close()
    
    return render_template('profile.html', 
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
                            pl_counts=pl_counts)

# 1. Add song to playlist endpoint
@app.route('/add_to_playlist/<int:song_id>', methods=['POST'])
def add_to_playlist(song_id):
    if 'user_id' not in session:
        return redirect('/login')
        
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Check if the song is already in the playlist (prevent duplicates)
    existing = conn.execute('SELECT * FROM playlists WHERE user_id = ? AND song_id = ?', 
                            (user_id, song_id)).fetchone()
    
    if not existing:
        conn.execute('INSERT INTO playlists (user_id, song_id) VALUES (?, ?)', (user_id, song_id))
        conn.commit()
        
    conn.close()
    return jsonify({'success': True, 'message': 'Song added to playlist'})

# 2. View user's playlist route
@app.route('/playlist')
def view_playlist():
    if 'user_id' not in session:
        return redirect('/login')
        
    conn = get_db_connection()
    # Fetch only the songs the user has added, using a JOIN
    playlist_songs = conn.execute('''
        SELECT songs.* FROM playlists 
        JOIN songs ON playlists.song_id = songs.id 
        WHERE playlists.user_id = ?
    ''', (session['user_id'],)).fetchall()
    conn.close()
    
    return render_template('playlist.html', songs=playlist_songs)

@app.route('/remove_from_playlist/<int:song_id>', methods=['POST'])
def remove_from_playlist(song_id):
    if 'user_id' not in session:
        return redirect('/login')
        
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Delete only the relevant song from the user's playlist
    conn.execute('DELETE FROM playlists WHERE user_id = ? AND song_id = ?', (user_id, song_id))
    conn.commit()
    conn.close()
    
    return redirect('/playlist') # Redirect back to the playlist page after removal

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
    
    conn = get_db_connection()
    song = conn.execute('SELECT * FROM songs WHERE id = ?', (song_id,)).fetchone()
    if not song:
        conn.close()
        return redirect('/')
    
    # Ensure a valid audio path — fall back to silent default if missing
    song = dict(song)
    if not song.get('file_path') or song['file_path'].strip() == '':
        song['file_path'] = 'default.mp3'
    
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
    
    return render_template('player.html', song=song, recommendations=recommendations)

if __name__ == '__main__':
    # Create sample user accounts
    conn = sqlite3.connect('music.db')
    cursor = conn.cursor()
    try:
        # Set username 'admin' with password '1234'
        hashed = generate_password_hash('1234')
        cursor.execute("INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)", ('admin', hashed))
        cursor.execute("INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)", ('test', hashed))
        conn.commit()
        print("Sample users (admin, 1234) created successfully!")
    except Exception as e:
        print("Failed to create sample users:", e)
    finally:
        conn.close()

    app.run(debug=True)

