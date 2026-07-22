import asyncio
import os
import threading

import discord
from discord import app_commands

import run_manager

SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1_lHNg3BbGKdyN4SfHhymjsAHNZ2BW8e36zVw1U0e3SM/edit?gid=0#gid=0"
)
_EDIT_INTERVAL_SECONDS = 5

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


class ControlPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="เปิด Google Sheet",
            style=discord.ButtonStyle.link,
            url=SPREADSHEET_URL,
            emoji="📊",
        ))

    @discord.ui.button(
        label="เริ่มดึงข้อมูล",
        style=discord.ButtonStyle.success,
        custom_id="au_result_date:run",
        emoji="▶️",
    )
    async def run_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⏳ กำลังเริ่มทำงาน...")
        message = await interaction.original_response()
        loop = asyncio.get_running_loop()
        last_line = {"text": ""}

        def progress_callback(msg):
            last_line["text"] = msg

        def done_callback(result):
            if result.get("success"):
                text = f"✅ เสร็จสิ้น! ดึงข้อมูลได้ {result.get('total_rows', 0)} รายการ\nดูผลลัพธ์ได้ที่ Google Sheet"
            else:
                text = f"❌ เกิดข้อผิดพลาด: {result.get('error')}"
            asyncio.run_coroutine_threadsafe(_safe_edit(message, text), loop)

        started = run_manager.start_run(progress_callback, done_callback)
        if not started:
            await _safe_edit(message, "⚠️ กำลังทำงานอยู่แล้ว กรุณารอสักครู่...")
            return

        asyncio.create_task(_periodic_update(message, last_line))


async def _periodic_update(message, last_line):
    while run_manager.state["running"]:
        await asyncio.sleep(_EDIT_INTERVAL_SECONDS)
        if not run_manager.state["running"]:
            break
        snippet = last_line["text"][-500:]
        await _safe_edit(message, f"⏳ กำลังทำงาน...\n```{snippet}```")


async def _safe_edit(message, content):
    try:
        await message.edit(content=content)
    except discord.HTTPException:
        pass


@tree.command(name="panel", description="แสดงปุ่มควบคุมสำหรับ Au Result Date")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📅 Au Result Date",
        description="กดปุ่มด้านล่างเพื่อเริ่มดึงข้อมูล หรือเปิดดู Google Sheet",
    )
    await interaction.response.send_message(embed=embed, view=ControlPanelView())


@client.event
async def on_ready():
    client.add_view(ControlPanelView())
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"Discord bot logged in as {client.user}")


def start_bot():
    """Start the Discord bot in a background thread, if a token is configured."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("DISCORD_BOT_TOKEN not set - Discord bot will not start")
        return

    def run():
        client.run(token)

    threading.Thread(target=run, daemon=True).start()
