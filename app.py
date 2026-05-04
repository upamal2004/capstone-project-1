from flask import Flask, render_template, request, redirect, session
import sqlite3

app = Flask(__name__)
app.secret_key = 'your_secret_key_here' # Session එක වැඩ කරන්න මේක ඕනේ

# Database එකට සම්බන්ධ වෙන function එක
def get_db_connection():
    conn = sqlite3.connect('music.db')
    conn.row_factory = sqlite3.Row # මේකෙන් අපිට column names පාවිච්චි කරන්න පුළුවන්
    return conn

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?', 
                            (username, password)).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect('/') # Login වුණාම Home page එකට යනවා
        else:
            return "Login අසාර්ථකයි! නැවත උත්සාහ කරන්න."
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        try:
            # යූසර්ව ඩේටාබේස් එකට ඇතුළත් කරනවා
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            conn.close()
            return redirect('/login') # රෙජිස්ටර් වුණාම ලොගින් පේජ් එකට යවනවා
        except sqlite3.IntegrityError:
            # යූසර් කෙනෙක් දැනටමත් ඒ නමින් ඉන්නවා නම් මේක වැඩ කරනවා
            return "මේ Username එක දැනටමත් පාවිච්චි වෙනවා! වෙන එකක් උත්සාහ කරන්න."
            
    return render_template('register.html')


# Home page එක
@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect('/login')
    
    conn = get_db_connection()
    # මුලින්ම නිකන් සින්දු ටිකක් පෙන්වන්න
    songs = conn.execute('SELECT * FROM songs LIMIT 10').fetchall()
    conn.close()
    return render_template('index.html', songs=songs)

@app.route('/recommend', methods=['POST'])
def recommend():
    genre = request.form.get('genre')
    mood = request.form.get('mood')
    
    conn = get_db_connection()
    
    # Rule-based Logic
    if mood == 'Happy':
        query = "SELECT * FROM songs WHERE genre = ? AND energy > 0.6"
    else:
        query = "SELECT * FROM songs WHERE genre = ? AND energy <= 0.6"
        
    songs = conn.execute(query, (genre,)).fetchall()
    conn.close()
    
    return render_template('index.html', songs=songs)

@app.route('/search', methods=['GET'])
def search_song():
    query = request.args.get('query')
    
    conn = get_db_connection()
    # සින්දුවේ නම ඇතුළේ අපි සර්ච් කරන වචනය තියෙනවද කියලා බලනවා (LIKE operator)
    songs = conn.execute("SELECT * FROM songs WHERE title LIKE ?", ('%' + query + '%',)).fetchall()
    conn.close()
    
    return render_template('index.html', songs=songs)

@app.route('/logout')
def logout():
    session.clear() # session එක අයින් කරනවා
    return redirect('/login')

if __name__ == '__main__':
    # සාම්පල් යූසර් කෙනෙක්ව ඇඩ් කරමු
    conn = sqlite3.connect('music.db')
    cursor = conn.cursor()
    try:
        # මම මෙතන Username එකට 'admin' සහ Password එකට '1234' දානවා
        cursor.execute("INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)", ('admin', '1234'))
        conn.commit()
        print("සාම්පල් යූසර් (admin, 1234) සාර්ථකව ඇඩ් කළා!")
    except Exception as e:
        print("යූසර් ඇඩ් කරන්න බැරි වුණා:", e)
    finally:
        conn.close()

    app.run(debug=True)