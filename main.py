import os
import asyncio
import threading
import datetime
import requests
from flask import Flask, request, redirect, render_template_string
import discord
from discord.ext import commands
from discord.ui import View, Button
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIRECT_URI = os.getenv("REDIRECT_URI")
OAUTH2_URL = os.getenv("OAUTH2_URL")
PORT = int(os.getenv("PORT", 5000))

# ⚠️ 서버 ID 및 주요 역할/채널 ID 설정
TARGET_GUILD_ID = "1551921173783257108"
TARGET_ROLE_ID = "1551935006975205377"
PURCHASE_LOG_CHANNEL_ID = 123456789012345678  # 구매로그가 뜰 채널 ID (숫자)
WELCOME_CHANNEL_ID = 1551939925065334834      # 입장 알람을 띄울 채널 ID (숫자)
GOODBYE_CHANNEL_ID = 1551941661330767952     # 퇴장 알람을 띄울 채널 ID (숫자)

ALLOWED_ADMIN_IDS = [
    "1503013871307456645",  # 관리자 ID
]

# MongoDB 연결 설정
MONGO_URI = os.getenv("MONGO_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["discord_bot_db"]
tokens_collection = db["tokens"]
users_collection = db["users"]       # 유저 포인트 정보 저장
items_collection = db["items"]       # 자판기 상품 정보 저장
orders_collection = db["orders"]     # 주문 내역 저장

def load_tokens():
    tokens = {}
    for doc in tokens_collection.find():
        tokens[str(doc["user_id"])] = doc["access_token"]
    return tokens

def save_token(user_id, access_token):
    tokens_collection.update_one(
        {"user_id": str(user_id)},
        {"$set": {"access_token": access_token}},
        upsert=True
    )

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>서버 인증</title>
    <style>
        body { background-color: #0f0f12; color: white; font-family: 'Segoe UI', sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background-color: #18181c; border: 1px solid #2b2b30; border-radius: 16px; padding: 32px; width: 360px; text-align: center; box-shadow: 0 8px 24px rgba(0,0,0,0.5); }
        .btn { background-color: #ffffff; color: #000000; border: none; border-radius: 8px; padding: 12px; width: 100%; font-weight: bold; font-size: 15px; cursor: pointer; margin-top: 20px; text-decoration: none; display: block; box-sizing: border-box; }
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

    save_token(user_id, access_token)

    if TARGET_GUILD_ID and TARGET_GUILD_ID != "너의_디스코드_서버_ID":
        add_headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}"
        payload = {"access_token": access_token}
        if TARGET_ROLE_ID and TARGET_ROLE_ID != "인증시_부여할_역할_ID":
            payload["roles"] = [TARGET_ROLE_ID]
        requests.put(url, json=payload, headers=add_headers)

    return f"<h1>인증 및 서버 가입 완료!</h1><p>{username}님, 정상적으로 처리되었습니다. 창을 닫으셔도 됩니다.</p>"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- [입장 및 퇴장 알람 이벤트] ---
@bot.event
async def on_member_join(member):
    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel:
        await channel.send(f"🎉 {member.mention}님, 서버에 오신 것을 환영합니다! 🥳")

@bot.event
async def on_member_remove(member):
    channel = member.guild.get_channel(GOODBYE_CHANNEL_ID)
    if channel:
        await channel.send(f"👋 **{member.name}**님이 서버를 나가셨습니다...")

# --- [자판기 및 패널 인터페이스 클래스] ---
class VendingView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="상품 보기", style=discord.ButtonStyle.success, custom_id="shop_view_items", emoji="🛒")
    async def view_items(self, interaction: discord.Interaction, button: Button):
        items = list(items_collection.find())
        if not items:
            await interaction.response.send_message("❌ 현재 등록된 상품이 없습니다.", ephemeral=True)
            return
        
        embed = discord.Embed(title="🎨 자비샵 상품 목록", description="구매할 상품의 번호나 이름을 확인하세요.", color=0x5865F2)
        for idx, item in enumerate(items, 1):
            embed.add_field(name=f"{idx}. {item['name']}", value=f"가격: {item['price']}원 / 역할ID: `<@{item['role_id']}>`", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="내 포인트", style=discord.ButtonStyle.primary, custom_id="shop_my_points", emoji="💰")
    async def my_points(self, interaction: discord.Interaction, button: Button):
        user_data = users_collection.find_one({"user_id": str(interaction.user.id)})
        points = user_data.get("points", 0) if user_data else 0
        await interaction.response.send_message(f"💳 현재 **{interaction.user.name}**님의 잔액은 **{points}원** 입니다.", ephemeral=True)

    @discord.ui.button(label="장바구니", style=discord.ButtonStyle.secondary, custom_id="shop_cart", emoji="🛍️")
    async def cart(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🛍️ 장바구니 기능은 준비 중입니다. 상품 보기 후 구매 명령어를 이용해 주세요!", ephemeral=True)

    @discord.ui.button(label="주문내역", style=discord.ButtonStyle.secondary, custom_id="shop_orders", emoji="📦")
    async def orders(self, interaction: discord.Interaction, button: Button):
        user_orders = list(orders_collection.find({"user_id": str(interaction.user.id)}).limit(5))
        if not user_orders:
            await interaction.response.send_message("📦 최근 주문 내역이 없습니다.", ephemeral=True)
            return
        
        embed = discord.Embed(title="📦 최근 주문 내역", color=0xFEE75C)
        for order in user_orders:
            embed.add_field(name=order['item_name'], value=f"구매 일시: {order['date']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="문의하기", style=discord.ButtonStyle.danger, custom_id="shop_ticket", emoji="💬")
    async def inquiry(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🎫 문의는 서버 내 티켓 생성 채널의 [티켓 열기] 버튼을 이용해 주세요!", ephemeral=True)

# --- [티켓 생성 뷰] ---
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="티켓 열기", style=discord.ButtonStyle.success, custom_id="create_ticket_btn", emoji="📩")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="🎫 문의 티켓")
        if not category:
            category = await guild.create_category("🎫 문의 티켓")
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        ticket_channel = await guild.create_text_channel(f"티켓-{interaction.user.name}", category=category, overwrites=overwrites)
        
        close_view = TicketCloseView()
        await ticket_channel.send(f"안녕하세요 {interaction.user.mention}님! 무엇을 도와드릴까요?", view=close_view)
        await interaction.response.send_message(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)

class TicketCloseView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="티켓 닫기", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 잠시 후 티켓 채널이 삭제됩니다...")
        await asyncio.sleep(3)
        await interaction.channel.delete()


@bot.event
async def on_ready():
    print(f"[Bot] 로그인 성공: {bot.user.name}")
    bot.add_view(VendingView())
    bot.add_view(TicketView())
    bot.add_view(TicketCloseView())

# --- [명령어: 자판기 패널 생성] ---
@bot.command(name="자판기세팅")
async def setup_vending(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 관리자만 사용할 수 있습니다.")
        return
    
    embed = discord.Embed(
        title="🎨 자비샵",
        description="아래 버튼으로 상품 확인, 내 포인트, 장바구니, 주문내역, 문의하기를 이용할 수 있습니다.",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=VendingView())
    await ctx.message.delete()

# --- [명령어: 티켓 패널 생성] ---
@bot.command(name="티켓세팅")
async def setup_ticket(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 관리자만 사용할 수 있습니다.")
        return
    
    embed = discord.Embed(
        title="서버 티켓",
        description="모든 문의는 티켓으로 부탁드립니다.\n\n아래 버튼을 클릭하면 티켓이 생성됩니다.",
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=TicketView())
    await ctx.message.delete()

# --- [명령어: 상품 추가/포인트지급/구매] ---
@bot.command(name="상품추가")
async def add_item(ctx, name: str, price: int, role_id: int):
    if not ctx.author.guild_permissions.administrator:
        return
    items_collection.update_one({"name": name}, {"$set": {"price": price, "role_id": str(role_id)}}, upsert=True)
    await ctx.send(f"✅ 상품 **[{name}]** (가격: {price}원) 등록 완료!")

@bot.command(name="포인트지급")
async def give_point(ctx, member: discord.Member, amount: int):
    if not ctx.author.guild_permissions.administrator:
        return
    users_collection.update_one({"user_id": str(member.id)}, {"$inc": {"points": amount}}, upsert=True)
    await ctx.send(f"💰 {member.mention}님에게 포인트 {amount}원이 지급되었습니다.")

@bot.command(name="구매")
async def buy_item(ctx, *, item_name: str):
    item = items_collection.find_one({"name": item_name})
    if not item:
        await ctx.send("❌ 존재하지 않는 상품입니다.")
        return
    
    user_doc = users_collection.find_one({"user_id": str(ctx.author.id)})
    my_points = user_doc.get("points", 0) if user_doc else 0
    price = item["price"]

    if my_points < price:
        await ctx.send(f"❌ 포인트가 부족합니다! (필요: {price}원, 보유: {my_points}원)")
        return

    users_collection.update_one({"user_id": str(ctx.author.id)}, {"$inc": {"points": -price}})
    
    role = ctx.guild.get_role(int(item["role_id"]))
    if role:
        await ctx.author.add_roles(role)

    orders_collection.insert_one({
        "user_id": str(ctx.author.id),
        "item_name": item_name,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    await ctx.send(f"🎉 성공적으로 **{item_name}**을(를) 구매했습니다! 역할이 지급되었습니다.")

    log_channel = ctx.guild.get_channel(PURCHASE_LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"💐 {ctx.author.name}님이 {item_name}을(를) 구매했습니다!")

# --- [구매후기 채널 권한 제어: 1시간 동안만 쓰기 가능] ---
@bot.command(name="구매후기열기")
async def open_review(ctx, member: discord.Member, channel: discord.TextChannel):
    if not ctx.author.guild_permissions.administrator:
        return
    
    await channel.set_permissions(member, send_messages=True, read_messages=True)
    await ctx.send(f"✅ {channel.mention} 채널에 {member.mention}님의 구매후기 작성 권한이 1시간 동안 부여되었습니다.")

    await asyncio.sleep(3600)
    await channel.set_permissions(member, send_messages=False)
    try:
        await member.send(f"⏰ {channel.mention} 채널의 구매후기 작성 시간이 만료되어 권한이 회수되었습니다.")
    except:
        pass


@bot.command(name="전체초대", aliases=["강제초대"])
async def force_join(ctx, limit: int = None):
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

    target_user_ids = user_ids[:limit] if limit else user_ids
    guild_id = ctx.guild.id
    msg = await ctx.send(f"⏳ 총 {len(target_user_ids)}명의 유저를 초대 중입니다...")

    success = 0
    fail = 0
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}

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

    await msg.edit(content=f"✅ **초대 완료!**\n- 성공: {success}명\n- 실패: {fail}명")

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(BOT_TOKEN)
