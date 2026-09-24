import os
import asyncio
import threading
import datetime
import requests
from flask import Flask, request, redirect, render_template_string
import discord
from discord.ext import commands
from discord.ui import View, Button, Select
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
TARGET_ROLE_ID = "1551935006975205377"         # 인증 시 부여할 '인증됨' 역할 ID
UNVERIFIED_ROLE_ID = "1551953671779131392"     # '미인증' 역할 ID
BUYER_ROLE_ID = "1551963997358522498"         # 상품 구매 시 부여할 '구매자' 역할 ID
PURCHASE_LOG_CHANNEL_ID = 1551946063764529262  # 구매로그가 뜰 채널 ID (숫자)
WELCOME_CHANNEL_ID = 1551939925065334834      # 입장 알람을 띄울 채널 ID (숫자)
GOODBYE_CHANNEL_ID = 1551941661330767952      # 퇴장 알람을 띄울 채널 ID (숫자)

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
        print(f"[에러] 토큰 발급 실패: {token_res.text}")
        return "토큰 발급 실패", 500
    
    access_token = token_res.json().get("access_token")
    user_headers = {"Authorization": f"Bearer {access_token}"}
    user_res = requests.get("https://discord.com/api/v10/users/@me", headers=user_headers)
    user_data = user_res.json()
    
    user_id = user_data.get("id")
    username = user_data.get("username")

    save_token(user_id, access_token)

    if TARGET_GUILD_ID and TARGET_GUILD_ID != "너의_디스코드_서버_ID":
        bot_headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        
        url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}"
        requests.put(url, json={"access_token": access_token}, headers=bot_headers)
        
        if TARGET_ROLE_ID and TARGET_ROLE_ID != "인증시_부여할_역할_ID":
            role_url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}/roles/{TARGET_ROLE_ID}"
            requests.put(role_url, headers={"Authorization": f"Bot {BOT_TOKEN}"})

        if UNVERIFIED_ROLE_ID and UNVERIFIED_ROLE_ID != "여기에_미인증_역할_ID_입력":
            remove_url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}/roles/{UNVERIFIED_ROLE_ID}"
            requests.delete(remove_url, headers={"Authorization": f"Bot {BOT_TOKEN}"})

    return f"<h1>인증 및 서버 가입 완료!</h1><p>{username}님, 정상적으로 처리되었습니다. 창을 닫으셔도 됩니다.</p>"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_member_join(member):
    if UNVERIFIED_ROLE_ID and UNVERIFIED_ROLE_ID != "여기에_미인증_역할_ID_입력":
        try:
            role = member.guild.get_role(int(UNVERIFIED_ROLE_ID))
            if role:
                await member.add_roles(role)
        except Exception as e:
            print(f"미인증 역할 부여 오류: {e}")

    channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if channel:
        await channel.send(f"🎉 {member.mention}님, 서버에 오신 것을 환영합니다! 🥳")

@bot.event
async def on_member_remove(member):
    channel = member.guild.get_channel(GOODBYE_CHANNEL_ID)
    if channel:
        await channel.send(f"👋 **{member.name}**님이 서버를 나가셨습니다...")

# --- [즉시 구매 셀렉트박스] ---
class BuySelect(Select):
    def __init__(self, items):
        options = []
        for item in items:
            name = item["name"]
            stock = item.get("stock", [])
            if name.endswith("무한") or (isinstance(stock, list) and len(stock) > 0):
                desc_text = "무제한 판매" if name.endswith("무한") else f"남은 재고: {len(stock)}개"
                options.append(discord.SelectOption(
                    label=name, 
                    description=f"가격: {item['price']}원 | {desc_text}", 
                    emoji="🛒"
                ))
        if not options:
            options.append(discord.SelectOption(label="구매 가능한 상품 없음", description="품절되었습니다.", emoji="❌"))
        super().__init__(placeholder="🛍️ 구매할 상품을 선택하세요!", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        if item_name == "구매 가능한 상품 없음":
            await interaction.response.send_message("❌ 구매 가능한 상품이 없습니다.", ephemeral=True)
            return

        item = items_collection.find_one({"name": item_name})
        if not item:
            await interaction.response.send_message("❌ 존재하지 않는 상품이거나 이미 품절된 상품입니다.", ephemeral=True)
            return

        is_infinite = item_name.endswith("무한")
        stock = item.get("stock", [])

        if not is_infinite and (not isinstance(stock, list) or len(stock) == 0):
            await interaction.response.send_message("❌ 죄송합니다! 해당 상품이 방금 품절되었습니다.", ephemeral=True)
            return

        user_doc = users_collection.find_one({"user_id": str(interaction.user.id)})
        my_points = user_doc.get("points", 0) if user_doc else 0
        price = item["price"]

        if my_points < price:
            await interaction.response.send_message(f"❌ 포인트가 부족합니다! (필요: {price}원, 보유: {my_points}원)", ephemeral=True)
            return

        users_collection.update_one({"user_id": str(interaction.user.id)}, {"$inc": {"points": -price}})
        
        orders_collection.insert_one({
            "user_id": str(interaction.user.id),
            "item_name": item_name,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        if is_infinite:
            given_content = item.get("content", "정보 없음")
        else:
            given_content = stock.pop(0)
            if len(stock) > 0:
                items_collection.update_one({"name": item_name}, {"$set": {"stock": stock}})
            else:
                items_collection.delete_one({"name": item_name})

        if BUYER_ROLE_ID and BUYER_ROLE_ID != "여기에_구매자_역할_ID_입력":
            try:
                buyer_role = interaction.guild.get_role(int(BUYER_ROLE_ID))
                if buyer_role and buyer_role not in interaction.user.roles:
                    await interaction.user.add_roles(buyer_role)
            except Exception as e:
                print(f"구매자 역할 부여 오류: {e}")

        try:
            await interaction.user.send(f"📦 **[{item_name}]** 구매가 완료되었습니다!\n\n[상품 정보 / 계정 내용]\n{given_content}")
            await interaction.response.send_message(f"🎉 구매 완료! **DM(개인 메시지)**으로 상품 정보가 발송되었습니다.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"⚠️ 구매는 되었으나 **DM 차단** 상태여서 상품 정보를 보내지 못했습니다!\n지급된 정보: `{given_content}`", ephemeral=True)

        log_channel = interaction.guild.get_channel(PURCHASE_LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"💐 {interaction.user.name}님이 {item_name}을(를) 구매했습니다!")

class BuySelectView(View):
    def __init__(self, items):
        super().__init__(timeout=None)
        self.add_item(BuySelect(items))

# --- [자판기 메인 뷰] ---
class VendingMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="상품 구매하기", style=discord.ButtonStyle.success, custom_id="vending_buy_menu", emoji="🛍️")
    async def open_buy_menu(self, interaction: discord.Interaction, button: Button):
        items = list(items_collection.find())
        if not items:
            await interaction.response.send_message("❌ 현재 등록된 상품이 없습니다.", ephemeral=True)
            return
        view = BuySelectView(items)
        await interaction.response.send_message("👇 아래 목록에서 구매할 상품을 선택하세요!", view=view, ephemeral=True)

    @discord.ui.button(label="내 포인트", style=discord.ButtonStyle.primary, custom_id="shop_my_points", emoji="💰")
    async def my_points(self, interaction: discord.Interaction, button: Button):
        user_data = users_collection.find_one({"user_id": str(interaction.user.id)})
        points = user_data.get("points", 0) if user_data else 0
        await interaction.response.send_message(f"💳 현재 **{interaction.user.name}**님의 잔액은 **{points}원** 입니다.", ephemeral=True)

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

# --- [티켓 관련 뷰] ---
class TicketCloseView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="티켓 닫기", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn", emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 잠시 후 티켓 채널이 삭제됩니다...")
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete()
        except:
            pass

class TicketMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="티켓 열기 (문의하기)", style=discord.ButtonStyle.success, custom_id="create_ticket_btn", emoji="📩")
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        
        if not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ 봇에게 **[채널 관리]** 권한이 없습니다!", ephemeral=True)
            return

        category = discord.utils.get(guild.categories, name="🎫 문의 티켓")
        if not category:
            try:
                category = await guild.create_category("🎫 문의 티켓")
            except:
                await interaction.response.send_message("❌ 카테고리를 생성할 권한이 없습니다.", ephemeral=True)
                return
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        try:
            ticket_channel = await guild.create_text_channel(f"티켓-{interaction.user.name}", category=category, overwrites=overwrites)
            close_view = TicketCloseView()
            await ticket_channel.send(f"안녕하세요 {interaction.user.mention}님! 무엇을 도와드릴까요?", view=close_view)
            await interaction.response.send_message(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 티켓 채널 생성 중 오류가 발생했습니다: {e}", ephemeral=True)

@bot.event
async def on_ready():
    print(f"[Bot] 로그인 성공: {bot.user.name}")
    bot.add_view(VendingMainView())
    bot.add_view(TicketMainView())
    bot.add_view(TicketCloseView())

# --- [관리자 전용 패널 강제 생성 명령어] ---
@bot.command(name="패널생성")
async def setup_panels(ctx):
    if not ctx.author.guild_permissions.administrator:
        return
    
    vending_embed = discord.Embed(
        title="마크닉 자판기",
        description="[상품 구매하기] 버튼을 누르면 목록에서 바로 구매하고 계정 정보를 받아볼 수 있습니다.",
        color=0x5865F2
    )
    await ctx.send(embed=vending_embed, view=VendingMainView())

    ticket_embed = discord.Embed(
        title="서버 문의 티켓",
        description="문의가 필요하신 분은 아래 버튼을 눌러 전용 채널을 생성해 주세요.",
        color=0x5865F2
    )
    await ctx.send(embed=ticket_embed, view=TicketMainView())
    await ctx.message.delete()

# --- [통합 상품 추가 명령어 (텍스트, 파일, URL 자동 지원)] ---
@bot.command(name="상품추가")
async def add_item(ctx, name: str, price: int, *, content: str = None):
    if not ctx.author.guild_permissions.administrator:
        return
    
    raw_content = ""

    # 1. 첨부파일(txt)이 있는 경우 파일 내용 읽기
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        try:
            file_bytes = await attachment.read()
            raw_content = file_bytes.decode("utf-8")
        except Exception as e:
            await ctx.send(f"❌ 파일을 읽는 중 오류가 발생했습니다: {e}")
            return
            
    # 2. 메시지 본문이나 텍스트로 링크(URL)나 텍스트가 같이 들어온 경우
    elif content:
        # 만약 본문에 http 링크가 포함되어 있다면 해당 주소의 텍스트 내용을 긁어올 수도 있음
        if content.startswith("http://") or content.startswith("https://"):
            try:
                res = requests.get(content.strip())
                if res.status_code == 200:
                    raw_content = res.text
                else:
                    raw_content = content # 링크 내용을 못 가져오면 그냥 링크 주소 자체를 재고로 등록
            except:
                raw_content = content
        else:
            raw_content = content

    if not raw_content:
        await ctx.send("❌ 재고 내용, 텍스트 파일, 또는 링크 중 하나를 함께 입력해주세요!")
        return

    stock_list = [line.strip() for line in raw_content.split("\n") if line.strip()]

    items_collection.update_one(
        {"name": name}, 
        {
            "$set": {
                "price": price, 
                "content": raw_content,
                "stock": stock_list
            }
        }, 
        upsert=True
    )
    await ctx.send(f"✅ 상품 **[{name}]** (가격: {price}원, 등록된 재고 수: {len(stock_list)}개) 등록 완료!")

@bot.command(name="상품삭제")
async def delete_item(ctx, *, name: str):
    if not ctx.author.guild_permissions.administrator:
        return
    result = items_collection.delete_one({"name": name})
    if result.deleted_count > 0:
        await ctx.send(f"🗑️ 상품 **[{name}]**이(가) 삭제되었습니다.")
    else:
        await ctx.send(f"❌ 존재하지 않는 상품입니다: **{name}**")

@bot.command(name="포인트지급")
async def give_point(ctx, member: discord.Member, amount: int):
    if not ctx.author.guild_permissions.administrator:
        return
    users_collection.update_one({"user_id": str(member.id)}, {"$inc": {"points": amount}}, upsert=True)
    await ctx.send(f"💰 {member.mention}님에게 포인트 {amount}원이 지급되었습니다.")

# --- [구매후기 제어 및 강제초대 명령어] ---
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
