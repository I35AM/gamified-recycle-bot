from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
import sqlite3
import time # 💡 [새로 추가] 투입 시간을 기록하기 위한 모듈

app = FastAPI(title="EcoCat Backend Server")

# ==========================================
# 💾 1. SQLite 데이터베이스 초기화 및 연결 설정
# ==========================================
DB_FILE = "ecocat.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            pw TEXT NOT NULL,
            nick TEXT NOT NULL,
            exp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1
        )
    ''')
    
    # 💡 [새로 추가] 쓰레기 투입 기록(History)을 저장할 테이블 생성
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            material TEXT NOT NULL,
            clean BOOLEAN NOT NULL,
            weight REAL NOT NULL,
            timestamp INTEGER NOT NULL,
            reported BOOLEAN DEFAULT 0,
            reviewed BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row 
    return conn

# ==========================================
# ⚡ 임시 상태 저장 
# ==========================================
kiosk_state = {
    "needs_new_pin": False,
    "current_pin": None  
}
active_pins = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 📦 데이터 전달 규격 (Pydantic 모델)
# ==========================================
class SignupData(BaseModel):
    id: str
    pw: str
    nick: str

class LoginData(BaseModel):
    id: str
    pw: str

class LinkPinData(BaseModel):
    user_id: str
    pin: str

class ThrowData(BaseModel):
    pin: str
    material: str
    clean: bool

# ==========================================
# 🚀 2. 회원 API 및 기록(History) API
# ==========================================

@app.post("/api/signup")
def signup(data: SignupData):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE id = ?", (data.id,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")

    cursor.execute("INSERT INTO users (id, pw, nick, exp, level) VALUES (?, ?, ?, ?, ?)",
                   (data.id, data.pw, data.nick, 0, 1))
    conn.commit()
    conn.close()
    return {"msg": "회원가입 성공"}

@app.post("/api/login")
def login(data: LoginData):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (data.id,))
    user = cursor.fetchone()
    conn.close()

    if not user or user["pw"] != data.pw:
        raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 틀렸습니다.")

    return {
        "msg": "로그인 성공", 
        "user_info": {"nick": user["nick"], "level": user["level"], "exp": user["exp"]}
    }

@app.get("/api/user_info/{user_id}")
def get_user_info(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT level, exp FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return {"level": user["level"], "exp": user["exp"]}

# 💡 [새로 추가] 특정 회원의 모든 투입 기록을 가져오는 API
@app.get("/api/user_info/{user_id}/history")
def get_user_history(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    # 시간순(과거->최신)으로 정렬해서 가져오기
    cursor.execute('''
        SELECT material, clean, weight, timestamp, reported, reviewed 
        FROM history 
        WHERE user_id = ? 
        ORDER BY timestamp ASC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()

    history_list = []
    for r in rows:
        history_list.append({
            "material": r["material"],
            "clean": bool(r["clean"]),
            "weight": r["weight"],
            "ts": r["timestamp"],
            "reported": bool(r["reported"]),
            "reviewed": bool(r["reviewed"])
        })
    return {"history": history_list}

@app.get("/api/check_id/{user_id}")
def check_id(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if user: return {"available": False}
    return {"available": True}

# ==========================================
# 📍 3. 키오스크 연동 API (수정됨)
# ==========================================

@app.post("/api/kiosk/trigger_pin")
def trigger_kiosk_pin():
    kiosk_state["needs_new_pin"] = True
    return {"message": "키오스크에 PIN 표시를 요청했습니다."}

@app.get("/api/kiosk/check_trigger")
def check_kiosk_trigger():
    if kiosk_state["needs_new_pin"]:
        kiosk_state["needs_new_pin"] = False 
        return {"trigger": True}
    return {"trigger": False}

@app.get("/api/kiosk/pin")
def generate_pin():
    new_pin = str(random.randint(100000, 999999)) 
    kiosk_state["current_pin"] = new_pin          
    active_pins[new_pin] = {"status": "waiting", "user_id": None} 
    return {"pin": new_pin}

@app.get("/api/kiosk/current_pin")
def get_current_pin():
    return {"pin": kiosk_state["current_pin"]}

@app.post("/api/kiosk/link")
def link_user_to_pin(data: LinkPinData):
    if data.pin not in active_pins:
        raise HTTPException(status_code=400, detail="유효하지 않은 PIN 번호입니다.")

    active_pins[data.pin]["status"] = "linked"
    active_pins[data.pin]["user_id"] = data.user_id
    return {"msg": f"쓰레기통(PIN: {data.pin})과 연동되었습니다! 이제 쓰레기를 버려주세요."}

@app.post("/api/kiosk/throw")
def throw_trash(data: ThrowData):
    session = active_pins.get(data.pin)

    if not session or session["status"] != "linked" or not session["user_id"]:
        raise HTTPException(status_code=400, detail="연동된 사용자가 없습니다.")

    user_id = session["user_id"]
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 💡 [새로 추가] History 테이블에 방금 버린 쓰레기 기록 남기기
    # 실시간 무게 센서가 없으므로 데모 시연을 위해 무게는 0.05 ~ 0.4kg 랜덤 생성
    simulated_weight = round(random.uniform(0.05, 0.40), 2)
    current_time = int(time.time() * 1000) # 스마트폰과 시간 단위를 맞추기 위해 밀리초 변환
    
    cursor.execute('''
        INSERT INTO history (user_id, material, clean, weight, timestamp)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, data.material, data.clean, simulated_weight, current_time))

    # Clean(오염 안됨)일 경우 경험치 10 추가
    if data.clean:
        cursor.execute("SELECT exp FROM users WHERE id = ?", (user_id,))
        current_exp = cursor.fetchone()["exp"]
        new_exp = current_exp + 10
        cursor.execute("UPDATE users SET exp = ? WHERE id = ?", (new_exp, user_id))
        
    conn.commit()
    
    # 최신 경험치 조회
    cursor.execute("SELECT exp FROM users WHERE id = ?", (user_id,))
    final_exp = cursor.fetchone()["exp"]
    conn.close()

    return {
        "msg": "처리 완료",
        "user_id": user_id,
        "current_exp": final_exp
    }

# ==========================================
# 👑 4. 관리자 전용 API
# ==========================================

@app.get("/api/admin/users")
def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nick, exp, level FROM users")
    users = cursor.fetchall()
    conn.close()

    user_list = [{"id": u["id"], "nick": u["nick"], "exp": u["exp"], "level": u["level"]} for u in users]
    return {"users": user_list}

@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    # 회원이 삭제될 때 그 사람의 투입 기록(history)도 같이 지워줍니다!
    cursor.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    
    conn.commit()
    conn.close()
    return {"msg": f"회원({user_id})이 성공적으로 삭제되었습니다."}

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")

# ==========================================
# 🏆 랭킹 API (새로 추가)
# ==========================================

@app.get("/api/ranking")
def get_ranking(limit: int = 10):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 💡 [수정됨] 정렬 기준 추가: 
    # 1. level 높은 순 (DESC)
    # 2. exp 높은 순 (DESC) 👈 새로 추가된 조건!
    # 3. 먼저 가입한 순 (ROWID ASC)
    cursor.execute('''
        SELECT id, nick, level, exp 
        FROM users 
        ORDER BY level DESC, exp DESC, ROWID ASC 
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()

    ranking_list = []
    for r in rows:
        ranking_list.append({
            "id": r["id"],
            "nick": r["nick"],
            "level": r["level"],
            "exp": r["exp"]
        })
        
    return {"ranking": ranking_list}