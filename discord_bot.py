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
        started_by = interaction.user.display_name
        await interaction.response.send_message(f"⏳ กำลังเริ่มทำงาน...\n👤 เริ่มโดย: {started_by}")
        message = await interaction.original_response()
        loop = asyncio.get_running_loop()
        progress = {"line": "", "percent": 0}

        def progress_callback(msg):
            progress["line"] = msg

        def progress_percent_callback(completed, total):
            progress["percent"] = int(completed / total * 100) if total else 0

        def done_callback(result):
            lines = [f"👤 เริ่มโดย: {started_by}"]
            if result.get("success"):
                lines.append(f"✅ เสร็จสิ้น! ดึงข้อมูลได้ {result.get('total_rows', 0)} รายการ")
                sc_credits = result.get("sc_credits_remaining")
                if sc_credits is not None:
                    lines.append(f"🎟️ ScrapeCreators เครดิตคงเหลือ: {sc_credits}")
                yt_units = result.get("yt_units_used")
                if yt_units:
                    lines.append(f"📺 YouTube: ใช้ไปประมาณ {yt_units} หน่วยในรอบนี้ (เช็คโควตารวมได้ที่ Google Cloud Console)")
                lines.append("ดูผลลัพธ์ได้ที่ Google Sheet")
            else:
                lines.append(f"❌ เกิดข้อผิดพลาด: {result.get('error')}")
            asyncio.run_coroutine_threadsafe(_safe_edit(message, "\n".join(lines)), loop)

        started = run_manager.start_run(
            progress_callback, done_callback, progress_percent_callback, started_by=started_by
        )
        if not started:
            current_runner = run_manager.state.get("started_by") or "ไม่ทราบ"
            await _safe_edit(message, f"⚠️ กำลังทำงานอยู่แล้ว (เริ่มโดย: {current_runner}) กรุณารอสักครู่...")
            return

        asyncio.create_task(_periodic_update(message, progress, started_by))


def _render_bar(percent, width=20):
    filled = int(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


async def _periodic_update(message, progress, started_by):
    while run_manager.state["running"]:
        await asyncio.sleep(_EDIT_INTERVAL_SECONDS)
        if not run_manager.state["running"]:
            break
        percent = progress["percent"]
        bar = _render_bar(percent)
        snippet = progress["line"][-400:]
        await _safe_edit(
            message,
            f"👤 เริ่มโดย: {started_by}\n⏳ กำลังทำงาน... {percent}%\n{bar}\n```{snippet}```",
        )


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
