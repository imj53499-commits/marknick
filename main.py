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
users_collection = db["users"]             # 유저 포인트 정보 저장
items_collection = db["items"]             # 자판기 상품 정보 저장
orders_collection = db["orders"]           # 주문 내역 저장
coupons_collection = db["coupons"]         # 쿠폰 정보 저장
coupon_logs_collection = db["coupon_logs"] # 쿠폰 사용 기록 저장

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
        bot_headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}"
        requests.put(url, json={"access_token": access_token}, headers=bot_headers)
        
        if TARGET_ROLE_ID:
            role_url = f"https://discord.com/api/v10/guilds/{TARGET_GUILD_ID}/members/{user_id}/roles/{TARGET_ROLE_ID}"
            requests.put(role_url, headers={"Authorization": f"Bot {BOT_TOKEN}"})

        if UNVERIFIED_ROLE_ID:
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
    if UNVERIFIED_ROLE_ID:
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

# --- [구매 확인 및 취소 뷰] ---
class ConfirmPurchaseView(View):
    def __init__(self, item_name):
        super().__init__(timeout=60)
        self.item_name = item_name

    @discord.ui.button(label="구매하기", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_buy(self, interaction: discord.Interaction, button: Button):
        item = items_collection.find_one({"name": self.item_name})
        if not item:
            await interaction.response.edit_message(content="❌ 존재하지 않는 상품이거나 이미 품절된 상품입니다.", view=None, embed=None)
            return

        is_infinite = item.get("is_infinite", False)
        stock = item.get("stock", [])

        if not is_infinite and (not isinstance(stock, list) or len(stock) == 0):
            await interaction.response.edit_message(content="❌ 죄송합니다! 해당 상품이 방금 품절되었습니다.", view=None, embed=None)
            return

        user_doc = users_collection.find_one({"user_id": str(interaction.user.id)})
        my_points = user_doc.get("points", 0) if user_doc else 0
        price = item["price"]

        if my_points < price:
            await interaction.response.edit_message(content=f"❌ 포인트가 부족합니다! (필요: {price}원, 보유: {my_points}원)", view=None, embed=None)
            return

        users_collection.update_one({"user_id": str(interaction.user.id)}, {"$inc": {"points": -price}})
        orders_collection.insert_one({
            "user_id": str(interaction.user.id),
            "item_name": self.item_name,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        if is_infinite:
            given_content = item.get("content", "정보 없음")
        else:
            given_content = stock.pop(0)
            if len(stock) > 0:
                items_collection.update_one({"name": self.item_name}, {"$set": {"stock": stock}})
            else:
                items_collection.delete_one({"name": self.item_name})

        if BUYER_ROLE_ID:
            try:
                buyer_role = interaction.guild.get_role(int(BUYER_ROLE_ID))
                if buyer_role and buyer_role not in interaction.user.roles:
                    await interaction.user.add_roles(buyer_role)
            except Exception as e:
                print(f"구매자 역할 부여 오류: {e}")

        try:
            await interaction.user.send(f"📦 **[{self.item_name}]** 구매가 완료되었습니다!\n\n[상품 정보 / 계정 내용]\n{given_content}")
            await interaction.response.edit_message(content=f"🎉 구매가 정상 완료되었습니다! **DM(개인 메시지)**으로 상품 정보가 발송되었습니다.", view=None, embed=None)
        except discord.Forbidden:
            await interaction.response.edit_message(content=f"⚠️ 구매는 되었으나 **DM 차단** 상태여서 상품 정보를 보내지 못했습니다!\n지급된 정보: `{given_content}`", view=None, embed=None)

        log_channel = interaction.guild.get_channel(PURCHASE_LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"💐 {interaction.user.name}님이 {self.item_name}을(를) 구매했습니다!")

    @discord.ui.button(label="취소", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_buy(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="❌ 구매가 취소되었습니다.", view=None, embed=None)

# --- [상품 선택 셀렉트박스] ---
class ItemSelect(Select):
    def __init__(self, items):
        options = []
        for item in items:
            name = item["name"]
            is_infinite = item.get("is_infinite", False)
            stock = item.get("stock", [])
            
            if is_infinite or (isinstance(stock, list) and len(stock) > 0):
                desc_text = "무제한 판매" if is_infinite else f"남은 재고: {len(stock)}개"
                options.append(discord.SelectOption(
                    label=name, 
                    description=f"가격: {item['price']}원 | {desc_text}", 
                    emoji="📦"
                ))
        if not options:
            options.append(discord.SelectOption(label="구매 가능한 상품 없음", description="품절되었습니다.", emoji="❌"))
        super().__init__(placeholder="🛒 구매할 상품을 선택하세요!", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        if item_name == "구매 가능한 상품 없음":
            await interaction.response.send_message("❌ 구매 가능한 상품이 없습니다.", ephemeral=True)
            return

        item = items_collection.find_one({"name": item_name})
        if not item:
            await interaction.response.send_message("❌ 존재하지 않는 상품이거나 이미 품절된 상품입니다.", ephemeral=True)
            return

        is_infinite = item.get("is_infinite", False)
        stock = item.get("stock", [])
        stock_status = "무제한 판매 상품" if is_infinite else f"남은 재고: {len(stock)}개"

        embed = discord.Embed(
            title=f"🛒 상품 확인: {item_name}",
            description=f"선택하신 상품이 맞는지 확인 후 하단의 **[구매하기]** 또는 **[취소]** 버튼을 눌러주세요.",
            color=0x5865F2
        )
        embed.add_field(name="💰 가격", value=f"{item['price']}원", inline=True)
        embed.add_field(name="📦 재고 상태", value=stock_status, inline=True)
        embed.add_field(name="📁 카테고리", value=item.get("category", "기본"), inline=True)

        view = ConfirmPurchaseView(item_name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ItemSelectView(View):
    def __init__(self, items):
        super().__init__(timeout=None)
        self.add_item(ItemSelect(items))

# --- [카테고리 선택 셀렉트박스] ---
class CategorySelect(Select):
    def __init__(self, categories):
        options = [
            discord.SelectOption(label=cat, description=f"📁 [{cat}] 카테고리 상품 보기", emoji="📂")
            for cat in categories
        ]
        super().__init__(placeholder="📁 상품 카테고리를 선택하세요!", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_category = self.values[0]
        items = list(items_collection.find({"category": selected_category}))
        
        if not items:
            await interaction.response.send_message(f"❌ **[{selected_category}]** 카테고리에 등록된 상품이 없습니다.", ephemeral=True)
            return

        view = ItemSelectView(items)
        await interaction.response.send_message(f"📂 **[{selected_category}]** 카테고리 상품 목록입니다. 구매할 상품을 선택하세요!", view=view, ephemeral=True)

class CategorySelectView(View):
    def __init__(self, categories):
        super().__init__(timeout=None)
        self.add_item(CategorySelect(categories))

# --- [메인 자판기 패널 뷰] ---
class VendingMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="상품 구매하기", style=discord.ButtonStyle.success, custom_id="vending_buy_menu", emoji="🛍️")
    async def open_buy_menu(self, interaction: discord.Interaction, button: Button):
        all_items = list(items_collection.find())
        if not all_items:
            await interaction.response.send_message("❌ 현재 등록된 상품이 없습니다.", ephemeral=True)
            return
        
        categories = list(set(item.get("category", "기본") for item in all_items))
        view = CategorySelectView(categories)
        await interaction.response.send_message("👇 원하시는 **카테고리**를 선택해주세요!", view=view, ephemeral=True)

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
    try:
        await bot.tree.sync()
        print("[Bot] 슬래시 명령어(/) 동기화 완료!")
    except Exception as e:
        print(f"[Bot] 슬래시 명령어 동기화 실패: {e}")

# --- [슬래시 명령어들] ---
@bot.tree.command(name="패널생성", description="자판기 및 문의 티켓 패널을 생성합니다.")
async def setup_panels(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return
    
    vending_embed = discord.Embed(
        title="마크닉 자판기",
        description="[상품 구매하기] 버튼을 누르면 카테고리별로 상품을 선택하여 구매하실 수 있습니다.",
        color=0x5865F2
    )
    await interaction.channel.send(embed=vending_embed, view=VendingMainView())

    ticket_embed = discord.Embed(
        title="서버 문의 티켓",
        description="문의가 필요하신 분은 아래 버튼을 눌러 전용 채널을 생성해 주세요.",
        color=0x5865F2
    )
    await interaction.channel.send(embed=ticket_embed, view=TicketMainView())
    await interaction.response.send_message("✅ 패널이 성공적으로 생성되었습니다!", ephemeral=True)

# 💡 상품 추가 (이름과 카테고리가 같으면 재고 누적 기능 탑재!)
@bot.tree.command(name="상품추가", description="일반 재고 상품을 추가합니다. 이름과 카테고리가 같으면 재고에 누적됩니다.")
async def add_item(interaction: discord.Interaction, category: str, name: str, price: int, content: str = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return
    
    raw_content = content or ""
    if not raw_content:
        await interaction.response.send_message("❌ 재고 내용이나 텍스트를 함께 입력해주세요!", ephemeral=True)
        return

    new_stock_list = [line.strip() for line in raw_content.split("\n") if line.strip()]
    existing_item = items_collection.find_one({"name": name, "category": category})

    if existing_item:
        current_stock = existing_item.get("stock", [])
        updated_stock = current_stock + new_stock_list
        
        items_collection.update_one(
            {"name": name, "category": category},
            {
                "$set": {
                    "price": price, 
                    "stock": updated_stock,
                    "is_infinite": False
                }
            }
        )
        await interaction.response.send_message(f"✅ 기존 상품 **[{name}]**에 재고 **{len(new_stock_list)}개**가 추가되었습니다! (총 재고: {len(updated_stock)}개)", ephemeral=True)
    else:
        items_collection.update_one(
            {"name": name, "category": category}, 
            {
                "$set": {
                    "category": category,
                    "name": name,
                    "price": price, 
                    "content": raw_content,
                    "stock": new_stock_list,
                    "is_infinite": False
                }
            }, 
            upsert=True
        )
        await interaction.response.send_message(f"✅ 신규 상품 **[{name}]** (카테고리: {category}, 가격: {price}원, 재고 수: {len(new_stock_list)}개) 등록 완료!", ephemeral=True)

@bot.tree.command(name="무한상품추가", description="재고가 무한인 상품을 추가합니다.")
async def add_infinite_item(interaction: discord.Interaction, category: str, name: str, price: int, content: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return

    items_collection.update_one(
        {"name": name, "category": category}, 
        {
            "$set": {
                "category": category,
                "name": name,
                "price": price, 
                "content": content,
                "stock": [],
                "is_infinite": True
            }
        }, 
        upsert=True
    )
    await interaction.response.send_message(f"♾️ 무한 상품 **[{name}]** (카테고리: {category}, 가격: {price}원) 등록 완료!", ephemeral=True)

@bot.tree.command(name="상품삭제", description="등록된 상품을 삭제합니다.")
async def delete_item(interaction: discord.Interaction, name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return
    
    result = items_collection.delete_one({"name": name})
    if result.deleted_count > 0:
        await interaction.response.send_message(f"🗑️ 상품 **[{name}]**이(가) 정상적으로 삭제되었습니다.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 존재하지 않는 상품입니다: **{name}**", ephemeral=True)

@bot.tree.command(name="포인트지급", description="특정 유저에게 포인트를 지급합니다.")
async def give_point(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return
    
    users_collection.update_one({"user_id": str(member.id)}, {"$inc": {"points": amount}}, upsert=True)
    await interaction.response.send_message(f"💰 {member.mention}님에게 포인트 {amount}원이 지급되었습니다.", ephemeral=True)

# --- [쿠폰 명령어들] ---
@bot.tree.command(name="쿠폰등록", description="새로운 쿠폰을 등록합니다.")
async def register_coupon(
    interaction: discord.Interaction, 
    쿠폰번호: str, 
    할인금액: int, 
    사용횟수제한: int, 
    대상: str, 
    특정유저: discord.Member = None
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return
    
    if 대상 not in ["모든사람", "특정사람"]:
        await interaction.response.send_message("❌ 대상은 '모든사람' 또는 '특정사람' 중 하나로 입력해주세요.", ephemeral=True)
        return

    if 대상 == "특정사람" and not 특정유저:
        await interaction.response.send_message("❌ '특정사람'을 대상으로 할 경우 유저를 지정해야 합니다.", ephemeral=True)
        return

    coupon_data = {
        "code": 쿠폰번호,
        "discount": 할인금액,
        "max_uses": 사용횟수제한,
        "target_type": 대상,
        "target_user_id": str(특정유저.id) if 특정유저 else None
    }

    coupons_collection.update_one({"code": 쿠폰번호}, {"$set": coupon_data}, upsert=True)
    target_text = "모든 사용자" if 대상 == "모든사람" else f"특정 유저 ({특정유저.mention})"
    await interaction.response.send_message(f"✅ 쿠폰 **[{쿠폰번호}]** 등록 완료!\n- 충전/할인 금액: {할인금액}원\n- 1인당 사용 횟수: {사용횟수제한}회\n- 대상: {target_text}", ephemeral=True)

@bot.tree.command(name="쿠폰삭제", description="등록된 쿠폰을 삭제합니다.")
async def delete_coupon(interaction: discord.Interaction, 쿠폰번호: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return
    
    result = coupons_collection.delete_one({"code": 쿠폰번호})
    if result.deleted_count > 0:
        await interaction.response.send_message(f"🗑️ 쿠폰 **[{쿠폰번호}]**이(가) 삭제되었습니다.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 존재하지 않는 쿠폰 번호입니다.", ephemeral=True)

@bot.tree.command(name="쿠폰사용", description="쿠폰을 입력하여 포인트를 충전/사용합니다.")
async def use_coupon(interaction: discord.Interaction, 쿠폰번호: str):
    coupon = coupons_collection.find_one({"code": 쿠폰번호})
    if not coupon:
        await interaction.response.send_message("❌ 존재하지 않거나 만료된 쿠폰 번호입니다.", ephemeral=True)
        return

    user_id = str(interaction.user.id)
    target_type = coupon.get("target_type")
    target_user_id = coupon.get("target_user_id")

    if target_type == "특정사람" and target_user_id and user_id != target_user_id:
        await interaction.response.send_message("❌ 이 쿠폰을 사용할 수 있는 대상이 아닙니다.", ephemeral=True)
        return

    max_uses = coupon.get("max_uses", 1)
    log = coupon_logs_collection.find_one({"user_id": user_id, "code": 쿠폰번호})
    current_uses = log.get("uses", 0) if log else 0

    if max_uses > 0 and current_uses >= max_uses:
        await interaction.response.send_message(f"❌ 이 쿠폰은 1인당 사용 횟수({max_uses}회)를 모두 소모하셨습니다.", ephemeral=True)
        return

    discount = coupon.get("discount", 0)

    coupon_logs_collection.update_one(
        {"user_id": user_id, "code": 쿠폰번호},
        {"$inc": {"uses": 1}},
        upsert=True
    )

    users_collection.update_one({"user_id": user_id}, {"$inc": {"points": discount}}, upsert=True)
    await interaction.response.send_message(f"🎉 쿠폰 사용 완료! **{discount}원**이 포인트로 충전되었습니다.", ephemeral=True)

@bot.tree.command(name="구매후기열기", description="특정 유저에게 구매후기 채널 작성 권한을 1시간 동안 부여합니다.")
async def open_review(interaction: discord.Interaction, member: discord.Member, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return
    
    await channel.set_permissions(member, send_messages=True, read_messages=True)
    await interaction.response.send_message(f"✅ {channel.mention} 채널에 {member.mention}님의 구매후기 작성 권한이 1시간 동안 부여되었습니다.")

    await asyncio.sleep(3600)
    await channel.set_permissions(member, send_messages=False)
    try:
        await member.send(f"⏰ {channel.mention} 채널의 구매후기 작성 시간이 만료되어 권한이 회수되었습니다.")
    except:
        pass

@bot.tree.command(name="전체초대", description="인증한 유저들을 서버로 강제 초대합니다.")
async def force_join(interaction: discord.Interaction, limit: int = None):
    is_admin = interaction.user.guild_permissions.administrator
    is_allowed_user = str(interaction.user.id) in ALLOWED_ADMIN_IDS

    if not (is_admin or is_allowed_user):
        await interaction.response.send_message("❌ 이 명령어는 지정된 관리자만 사용할 수 있습니다!", ephemeral=True)
        return

    tokens = load_tokens()
    user_ids = list(tokens.keys())

    if not user_ids:
        await interaction.response.send_message("❌ 아직 인증을 완료한 유저가 없습니다.", ephemeral=True)
        return

    target_user_ids = user_ids[:limit] if limit else user_ids
    guild_id = interaction.guild_id
    
    await interaction.response.send_message(f"⏳ 총 {len(target_user_ids)}명의 유저를 초대 중입니다...", ephemeral=True)

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

    try:
        await interaction.followup.send(f"✅ **초대 완료!**\n- 성공: {success}명\n- 실패: {fail}명", ephemeral=True)
    except:
        pass

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(BOT_TOKEN)
