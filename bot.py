import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ─── Настрой роли и пороги поинтов здесь ───────────────────────────────────
# Формат: (минимум поинтов, название роли на твоём сервере)
ROLE_THRESHOLDS = [
    (0,   "Новичок"),
    (10,  "Участник"),
    (25,  "Активист"),
    (50,  "Ветеран"),
    (100, "Легенда"),
]
# ────────────────────────────────────────────────────────────────────────────

# ─── Keep-alive сервер (чтобы Render не усыплял бота) ────────────────────────
class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        pass  # отключаем лишние логи

def run_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), KeepAlive)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ─── База данных ─────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("points.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS points (
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            points    INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    conn.commit()
    conn.close()

def get_points(guild_id: int, user_id: int) -> int:
    conn = sqlite3.connect("points.db")
    row = conn.execute(
        "SELECT points FROM points WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    ).fetchone()
    conn.close()
    return row[0] if row else 0

def add_points(guild_id: int, user_id: int, amount: int) -> int:
    conn = sqlite3.connect("points.db")
    conn.execute("""
        INSERT INTO points (guild_id, user_id, points) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + excluded.points
    """, (str(guild_id), str(user_id), amount))
    conn.commit()
    new_total = conn.execute(
        "SELECT points FROM points WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id))
    ).fetchone()[0]
    conn.close()
    return new_total

def remove_points(guild_id: int, user_id: int, amount: int) -> int:
    conn = sqlite3.connect("points.db")
    current = get_points(guild_id, user_id)
    new_val = max(0, current - amount)
    conn.execute("""
        INSERT INTO points (guild_id, user_id, points) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET points = excluded.points
    """, (str(guild_id), str(user_id), new_val))
    conn.commit()
    conn.close()
    return new_val

def get_leaderboard(guild_id: int, limit: int = 10):
    conn = sqlite3.connect("points.db")
    rows = conn.execute(
        "SELECT user_id, points FROM points WHERE guild_id=? ORDER BY points DESC LIMIT ?",
        (str(guild_id), limit)
    ).fetchall()
    conn.close()
    return rows

# ─── Логика ролей ─────────────────────────────────────────────────────────────
async def update_role(member: discord.Member, total_points: int):
    target_role_name = ROLE_THRESHOLDS[0][1]
    for threshold, role_name in ROLE_THRESHOLDS:
        if total_points >= threshold:
            target_role_name = role_name

    role_names = {r for _, r in ROLE_THRESHOLDS}
    target_role = discord.utils.get(member.guild.roles, name=target_role_name)

    if target_role is None:
        return

    roles_to_remove = [
        r for r in member.roles
        if r.name in role_names and r != target_role
    ]
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove, reason="Автообновление ранга")
    if target_role not in member.roles:
        await member.add_roles(target_role, reason="Автообновление ранга")

# ─── Проверка прав ────────────────────────────────────────────────────────────
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator or \
           interaction.user.guild_permissions.manage_roles

# ─── Команды ──────────────────────────────────────────────────────────────────
@tree.command(name="give", description="[Админ] Выдать поинты участнику")
@app_commands.describe(member="Участник", amount="Количество поинтов")
async def give(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Только админы могут выдавать поинты.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Количество должно быть больше 0.", ephemeral=True)
        return

    total = add_points(interaction.guild_id, member.id, amount)
    await update_role(member, total)

    embed = discord.Embed(color=0x57F287)
    embed.description = f"✅ **{member.display_name}** получает **+{amount} поинтов**\n🏅 Итого: **{total}**"
    await interaction.response.send_message(embed=embed)


@tree.command(name="remove", description="[Админ] Снять поинты у участника")
@app_commands.describe(member="Участник", amount="Количество поинтов")
async def remove(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Только админы могут снимать поинты.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Количество должно быть больше 0.", ephemeral=True)
        return

    total = remove_points(interaction.guild_id, member.id, amount)
    await update_role(member, total)

    embed = discord.Embed(color=0xED4245)
    embed.description = f"🔻 **{member.display_name}** теряет **{amount} поинтов**\n🏅 Итого: **{total}**"
    await interaction.response.send_message(embed=embed)


@tree.command(name="points", description="Посмотреть количество поинтов")
@app_commands.describe(member="Участник (оставь пустым для себя)")
async def points(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    total = get_points(interaction.guild_id, target.id)

    current_rank = ROLE_THRESHOLDS[0][1]
    next_rank = None
    next_threshold = None
    for i, (threshold, role_name) in enumerate(ROLE_THRESHOLDS):
        if total >= threshold:
            current_rank = role_name
            if i + 1 < len(ROLE_THRESHOLDS):
                next_threshold, next_rank = ROLE_THRESHOLDS[i + 1]

    embed = discord.Embed(title=f"🏅 Поинты — {target.display_name}", color=0x5865F2)
    embed.add_field(name="Поинты", value=str(total), inline=True)
    embed.add_field(name="Ранг", value=current_rank, inline=True)
    if next_rank:
        embed.add_field(name="До следующего ранга", value=f"{next_threshold - total} поинтов → {next_rank}", inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="leaderboard", description="Топ участников по поинтам")
async def leaderboard(interaction: discord.Interaction):
    rows = get_leaderboard(interaction.guild_id)
    if not rows:
        await interaction.response.send_message("Пока никто не заработал поинты.", ephemeral=True)
        return

    embed = discord.Embed(title="🏆 Таблица лидеров", color=0xFEE75C)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (user_id, pts) in enumerate(rows):
        prefix = medals[i] if i < 3 else f"`{i+1}.`"
        lines.append(f"{prefix} <@{user_id}> — **{pts}** поинтов")
    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed)


# ─── Запуск ──────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    init_db()
    await tree.sync()
    print(f"✅ Бот запущен: {bot.user} | Slash-команды синхронизированы")

bot.run(TOKEN)
