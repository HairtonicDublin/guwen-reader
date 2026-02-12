"""
古诗文学习软件 - Flask 后端
功能：点词解释、划选句子解释、生词本、EPUB/PDF上传
"""

from flask import Flask, render_template, request, jsonify, send_from_directory
import sqlite3
import os
import json
from datetime import datetime
import requests
from werkzeug.utils import secure_filename

# PDF 和 EPUB 解析
import pdfplumber
from ebooklib import epub
from bs4 import BeautifulSoup

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'epub'}

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', 'sk-cae2748e8598422babdd661c334a70f0')
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'

# 获取应用根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 上传目录使用绝对路径
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')

# 确保目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'static'), exist_ok=True)

# 数据库路径
DB_PATH = os.path.join(BASE_DIR, 'guwen_reader.db')

def get_db_path():
    return DB_PATH

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    
    # 书籍表
    c.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            content TEXT,
            file_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 生词本表
    c.execute('''
        CREATE TABLE IF NOT EXISTS notebook (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            word TEXT,
            sentence TEXT,
            explanation TEXT,
            type TEXT,  -- 'word' 或 'sentence'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (book_id) REFERENCES books(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# 初始化数据库
init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def extract_text_from_pdf(filepath):
    """从 PDF 提取文本"""
    text = ""
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
    except Exception as e:
        print(f"PDF 解析错误: {e}")
    return text

def extract_text_from_epub(filepath):
    """从 EPUB 提取文本和书名"""
    text = ""
    title = None
    try:
        book = epub.read_epub(filepath)
        # 获取书名
        title = book.get_metadata('DC', 'title')
        if title:
            title = title[0][0]  # 提取标题字符串
        
        for item in book.get_items():
            if item.get_type() == 9:  # ITEM_DOCUMENT
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                text += soup.get_text() + "\n\n"
    except Exception as e:
        print(f"EPUB 解析错误: {e}")
    return text, title

def extract_title_from_pdf(filepath):
    """从 PDF 提取标题"""
    try:
        with pdfplumber.open(filepath) as pdf:
            if pdf.metadata and pdf.metadata.get('Title'):
                return pdf.metadata.get('Title')
    except:
        pass
    return None

def call_deepseek_api(prompt, system_prompt=None):
    """调用 DeepSeek API"""
    if DEEPSEEK_API_KEY == 'YOUR_DEEPSEEK_API_KEY':
        return "请先配置 DeepSeek API Key（在 app.py 中设置 DEEPSEEK_API_KEY 或环境变量）"
    
    headers = {
        'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
        'Content-Type': 'application/json'
    }
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    
    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000
    }
    
    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result['choices'][0]['message']['content']
    except requests.exceptions.RequestException as e:
        return f"API 请求失败: {str(e)}"
    except (KeyError, IndexError) as e:
        return f"解析响应失败: {str(e)}"

# ==================== 路由 ====================

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/notebook')
def notebook_page():
    """生词本页面"""
    return render_template('notebook.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """上传文件"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # 添加时间戳避免重名
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # 提取文本和书名
        file_type = ext.lower().replace('.', '')
        book_title = None
        
        if file_type == 'pdf':
            content = extract_text_from_pdf(filepath)
            book_title = extract_title_from_pdf(filepath)
        elif file_type == 'epub':
            content, book_title = extract_text_from_epub(filepath)
        else:
            content = ""
        
        # 如果没有提取到书名，使用文件名
        if not book_title:
            book_title = name
        
        # 保存到数据库
        conn = sqlite3.connect(get_db_path())
        c = conn.cursor()
        c.execute('''
            INSERT INTO books (title, filename, content, file_type)
            VALUES (?, ?, ?, ?)
        ''', (book_title, filename, content, file_type))
        book_id = c.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'book_id': book_id,
            'title': book_title,
            'content': content
        })
    
    return jsonify({'error': '不支持的文件格式'}), 400

@app.route('/api/books', methods=['GET'])
def get_books():
    """获取所有书籍"""
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    c.execute('SELECT id, title, file_type, created_at FROM books ORDER BY created_at DESC')
    books = [{'id': row[0], 'title': row[1], 'file_type': row[2], 'created_at': row[3]} 
             for row in c.fetchall()]
    conn.close()
    return jsonify(books)

@app.route('/api/book/<int:book_id>', methods=['GET'])
def get_book(book_id):
    """获取单本书籍内容"""
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    c.execute('SELECT id, title, content, file_type FROM books WHERE id = ?', (book_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return jsonify({
            'id': row[0],
            'title': row[1],
            'content': row[2],
            'file_type': row[3]
        })
    return jsonify({'error': '书籍不存在'}), 404

@app.route('/api/book/<int:book_id>', methods=['DELETE'])
def delete_book(book_id):
    """删除书籍"""
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    c.execute('DELETE FROM books WHERE id = ?', (book_id,))
    c.execute('DELETE FROM notebook WHERE book_id = ?', (book_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/explain/word', methods=['POST'])
def explain_word():
    """解释单个词语"""
    data = request.json
    word = data.get('word', '').strip()
    context = data.get('context', '')  # 上下文
    book_id = data.get('book_id')
    
    if not word:
        return jsonify({'error': '词语不能为空'}), 400
    
    system_prompt = """你是一位精通古汉语和现代汉语的学者。请为用户解释词语的含义。
回答格式：
1. 【读音】标注拼音（如有多音字请都列出）
2. 【古文释义】在古文中的含义
3. 【现代用法】在现代汉语中的常见用法和含义
4. 【相关词汇】列出2-3个相关的近义词或关联词

注意：回答简洁明了，不需要提供例句。"""
    
    prompt = f"请解释这个字/词：「{word}」"
    if context:
        prompt += f"\n\n该词出现在以下上下文中：\n{context}"
    
    explanation = call_deepseek_api(prompt, system_prompt)
    
    # 保存到生词本（避免重复）
    if book_id:
        conn = sqlite3.connect(get_db_path())
        c = conn.cursor()
        # 检查是否已存在
        c.execute('SELECT id FROM notebook WHERE book_id = ? AND word = ? AND type = ?', 
                  (book_id, word, 'word'))
        existing = c.fetchone()
        
        if existing:
            # 更新已有记录
            c.execute('UPDATE notebook SET explanation = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?',
                      (explanation, existing[0]))
        else:
            # 插入新记录
            c.execute('''
                INSERT INTO notebook (book_id, word, explanation, type)
                VALUES (?, ?, ?, 'word')
            ''', (book_id, word, explanation))
        conn.commit()
        conn.close()
    
    return jsonify({
        'word': word,
        'explanation': explanation
    })

@app.route('/api/explain/sentence', methods=['POST'])
def explain_sentence():
    """解释句子"""
    data = request.json
    sentence = data.get('sentence', '').strip()
    book_id = data.get('book_id')
    
    if not sentence:
        return jsonify({'error': '句子不能为空'}), 400
    
    system_prompt = """你是一位精通古汉语和现代汉语的学者。请为用户解释选中的句子。
回答格式：
1. 【现代文翻译】用现代白话文翻译这句话
2. 【重点字词】对关键词汇标注读音并解释含义
3. 【句法分析】简要分析句子的语法结构（如果是古文，说明特殊句式）

注意：回答简洁明了，不需要提供例句。"""
    
    prompt = f"请解释这句话：\n「{sentence}」"
    
    explanation = call_deepseek_api(prompt, system_prompt)
    
    # 保存到生词本（避免重复）
    if book_id:
        conn = sqlite3.connect(get_db_path())
        c = conn.cursor()
        # 检查是否已存在
        c.execute('SELECT id FROM notebook WHERE book_id = ? AND sentence = ? AND type = ?', 
                  (book_id, sentence, 'sentence'))
        existing = c.fetchone()
        
        if existing:
            # 更新已有记录
            c.execute('UPDATE notebook SET explanation = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?',
                      (explanation, existing[0]))
        else:
            # 插入新记录
            c.execute('''
                INSERT INTO notebook (book_id, sentence, explanation, type)
                VALUES (?, ?, ?, 'sentence')
            ''', (book_id, sentence, explanation))
        conn.commit()
        conn.close()
    
    return jsonify({
        'sentence': sentence,
        'explanation': explanation
    })

@app.route('/api/notebook', methods=['GET'])
def get_notebook():
    """获取生词本所有记录"""
    book_id = request.args.get('book_id', type=int)
    
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    
    if book_id:
        c.execute('''
            SELECT n.id, n.book_id, b.title, n.word, n.sentence, n.explanation, n.type, n.created_at
            FROM notebook n
            LEFT JOIN books b ON n.book_id = b.id
            WHERE n.book_id = ?
            ORDER BY n.created_at DESC
        ''', (book_id,))
    else:
        c.execute('''
            SELECT n.id, n.book_id, b.title, n.word, n.sentence, n.explanation, n.type, n.created_at
            FROM notebook n
            LEFT JOIN books b ON n.book_id = b.id
            ORDER BY n.created_at DESC
        ''')
    
    items = []
    for row in c.fetchall():
        items.append({
            'id': row[0],
            'book_id': row[1],
            'book_title': row[2],
            'word': row[3],
            'sentence': row[4],
            'explanation': row[5],
            'type': row[6],
            'created_at': row[7]
        })
    conn.close()
    return jsonify(items)

@app.route('/api/notebook/<int:item_id>', methods=['DELETE'])
def delete_notebook_item(item_id):
    """删除生词本记录"""
    try:
        db_path = get_db_path()
        print(f"[DEBUG] 删除记录 ID: {item_id}, 数据库路径: {db_path}")
        
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # 先检查记录是否存在
        c.execute('SELECT id, word, sentence FROM notebook WHERE id = ?', (item_id,))
        record = c.fetchone()
        print(f"[DEBUG] 查询结果: {record}")
        
        if not record:
            # 列出所有记录的 ID 用于调试
            c.execute('SELECT id FROM notebook')
            all_ids = [row[0] for row in c.fetchall()]
            print(f"[DEBUG] 数据库中所有 ID: {all_ids}")
            conn.close()
            return jsonify({'success': False, 'error': f'记录不存在，ID: {item_id}'}), 404
        
        c.execute('DELETE FROM notebook WHERE id = ?', (item_id,))
        deleted_count = c.rowcount
        print(f"[DEBUG] 删除行数: {deleted_count}")
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"[ERROR] 删除错误: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/notebook/stats', methods=['GET'])
def get_notebook_stats():
    """获取生词本统计"""
    book_id = request.args.get('book_id', type=int)
    
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    
    if book_id:
        # 该书词数
        c.execute("SELECT COUNT(*) FROM notebook WHERE type = 'word' AND book_id = ?", (book_id,))
        word_count = c.fetchone()[0]
        
        # 该书句数
        c.execute("SELECT COUNT(*) FROM notebook WHERE type = 'sentence' AND book_id = ?", (book_id,))
        sentence_count = c.fetchone()[0]
        
        # 该书今日新增
        c.execute("""
            SELECT COUNT(*) FROM notebook 
            WHERE DATE(created_at) = DATE('now') AND book_id = ?
        """, (book_id,))
        today_count = c.fetchone()[0]
    else:
        # 总词数
        c.execute("SELECT COUNT(*) FROM notebook WHERE type = 'word'")
        word_count = c.fetchone()[0]
        
        # 总句数
        c.execute("SELECT COUNT(*) FROM notebook WHERE type = 'sentence'")
        sentence_count = c.fetchone()[0]
        
        # 今日新增
        c.execute("""
            SELECT COUNT(*) FROM notebook 
            WHERE DATE(created_at) = DATE('now')
        """)
        today_count = c.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'word_count': word_count,
        'sentence_count': sentence_count,
        'today_count': today_count,
        'total_count': word_count + sentence_count
    })

if __name__ == '__main__':
    import threading
    import webview
    
    def start_server():
        app.run(host='127.0.0.1', port=5000, debug=False, threaded=True, use_reloader=False)
    
    # 在后台线程启动 Flask 服务器
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    # 创建桌面应用窗口
    webview.create_window(
        '古诗文学习',
        'http://127.0.0.1:5000',
        width=1400,
        height=900,
        resizable=True,
        min_size=(1000, 700)
    )
    webview.start()
