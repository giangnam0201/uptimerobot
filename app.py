import streamlit as st
import threading
import os
import asyncio
import sys
import time

# --- 1. THE "ULTIMATE" GLOBAL LOCK ---
if "bot_lock" not in sys.modules:
    sys.modules["bot_lock"] = True
    FIRST_RUN = True
else:
    FIRST_RUN = False

# --- 2. STREAMLIT UI ---
st.set_page_config(page_title="Bot Server", page_icon="🚀")
st.title("Service Status: Online ✅")
st.write("The bot is running in the background.")

# BRIDGE: Injects Streamlit Secrets into environment
for key, value in st.secrets.items():
    os.environ[key] = str(value)

# --- 3. YOUR CODE (AUTO-REFRESH DASHBOARD) ---
RAW_CODE = '''
# uptime_monitor_bot.py
import discord
from discord import app_commands, ui, ButtonStyle
from discord.ext import tasks, commands
import requests
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import urllib.parse

class WebsiteMonitor:
    def __init__(self, name: str, url: str, check_interval: int = 60, timeout: int = 10):
        self.name = name
        self.url = url
        self.check_interval = check_interval
        self.timeout = timeout
        self.is_up = True
        self.last_checked = None
        self.last_up_time = None
        self.response_times = []
        self.downtime_start = None
        self.total_downtime = 0
        self.consecutive_failures = 0
        self.failure_threshold = 2  # Alert after 2 consecutive failures
        self.status_history = []  # Keep last 50 status changes
        self.last_check_succeeded = None
        
    def add_status_record(self, status: bool, response_time: float = 0, error: str = ""):
        """Add status change to history"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "response_time": response_time,
            "error": error
        }
        self.status_history.append(record)
        if len(self.status_history) > 50:
            self.status_history.pop(0)
            
    def get_avg_response_time(self) -> float:
        """Get average response time over last 10 checks"""
        if not self.response_times:
            return 0
        recent = self.response_times[-10:]
        return sum(recent) / len(recent)
        
    def get_uptime_percentage(self, hours: int = 24) -> float:
        """Calculate uptime percentage over last N hours"""
        if not self.status_history:
            return 100.0
            
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_records = [
            r for r in self.status_history 
            if datetime.fromisoformat(r["timestamp"]) > cutoff_time
        ]
        
        if not recent_records:
            return 100.0
            
        up_count = sum(1 for r in recent_records if r["status"])
        return (up_count / len(recent_records)) * 100

class UptimeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        
        self.monitors: Dict[str, WebsiteMonitor] = {}
        self.alert_role = "@everyone"  # Can be configured
        self.dashboard_messages = {}  # Store dashboard message IDs per guild
        self.load_monitors()
        self.load_dashboard_messages()
        
    async def setup_hook(self):
        """Initialize the bot"""
        self.check_websites.start()
        self.update_dashboard.start()  # Start dashboard auto-refresh
        await self.tree.sync()
        print("✅ Bot ready! Monitoring {} websites".format(len(self.monitors)))
        
    def load_monitors(self):
        """Load monitors from JSON file"""
        try:
            with open("monitors.json", "r") as f:
                data = json.load(f)
                for name, monitor_data in data.items():
                    monitor = WebsiteMonitor(
                        name=monitor_data["name"],
                        url=monitor_data["url"],
                        check_interval=monitor_data.get("check_interval", 60),
                        timeout=monitor_data.get("timeout", 10)
                    )
                    monitor.is_up = monitor_data.get("is_up", True)
                    self.monitors[name] = monitor
        except FileNotFoundError:
            pass
            
    def save_monitors(self):
        """Save monitors to JSON file"""
        data = {}
        for name, monitor in self.monitors.items():
            data[name] = {
                "name": monitor.name,
                "url": monitor.url,
                "check_interval": monitor.check_interval,
                "timeout": monitor.timeout,
                "is_up": monitor.is_up
            }
        with open("monitors.json", "w") as f:
            json.dump(data, f, indent=2)
            
    def load_dashboard_messages(self):
        """Load dashboard message IDs"""
        try:
            with open("dashboard_messages.json", "r") as f:
                self.dashboard_messages = json.load(f)
        except FileNotFoundError:
            self.dashboard_messages = {}
            
    def save_dashboard_messages(self):
        """Save dashboard message IDs"""
        with open("dashboard_messages.json", "w") as f:
            json.dump(self.dashboard_messages, f, indent=2)
            
    # --- AUTO-REFRESH DASHBOARD (Every 5 seconds) ---
    @tasks.loop(seconds=5)
    async def update_dashboard(self):
        """Auto-refresh dashboard every 5 seconds"""
        if not self.dashboard_messages:
            return
            
        for guild_id_str, message_data in self.dashboard_messages.items():
            guild_id = int(guild_id_str)
            channel_id = message_data["channel_id"]
            message_id = message_data["message_id"]
            
            guild = self.get_guild(guild_id)
            if not guild:
                continue
                
            channel = guild.get_channel(channel_id)
            if not channel:
                continue
                
            try:
                message = await channel.fetch_message(message_id)
                embed = self.generate_dashboard_embed()
                view = DashboardView(bot)  # Add interactive buttons
                await message.edit(embed=embed, view=view)
            except discord.NotFound:
                # Message was deleted, remove from tracking
                del self.dashboard_messages[guild_id_str]
                self.save_dashboard_messages()
            except discord.Forbidden:
                pass
            except Exception as e:
                print("Dashboard update error: {}".format(str(e)))
                
    @update_dashboard.before_loop
    async def before_dashboard_update(self):
        """Wait for bot to be ready before starting dashboard updates"""
        await self.wait_until_ready()
        
    def generate_dashboard_embed(self) -> discord.Embed:
        """Generate the dashboard embed with real-time metrics"""
        total_sites = len(self.monitors)
        
        if total_sites == 0:
            embed = discord.Embed(
                title="🎯 Uptime Monitoring Dashboard",
                description="No websites are being monitored.",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            embed.add_field(
                name="Get Started",
                value="Use `/monitor add` to add your first website!",
                inline=False
            )
            return embed
            
        up_sites = sum(1 for m in self.monitors.values() if m.is_up)
        down_sites = total_sites - up_sites
        
        # Calculate overall system health
        health_color = discord.Color.green() if down_sites == 0 else discord.Color.red()
        uptime_pct = (up_sites / total_sites) * 100
        
        embed = discord.Embed(
            title="🎯 Uptime Monitoring Dashboard",
            description="Auto-refreshing every 5 seconds | {} websites monitored".format(total_sites),
            color=health_color,
            timestamp=datetime.now()
        )
        
        # System Overview
        embed.add_field(
            name="📊 System Overview",
            value=(
                "🟢 Online: **{}**\\n"
                "🔴 Offline: **{}**\\n"
                "📈 Health: **{:.1f}%**"
            ).format(up_sites, down_sites, uptime_pct),
            inline=False
        )
        
        # Individual Site Details
        sites_text = ""
        for name, monitor in sorted(self.monitors.items()):
            status_emoji = "🟢" if monitor.is_up else "🔴"
            avg_time = monitor.get_avg_response_time()
            uptime_24h = monitor.get_uptime_percentage(24)
            
            # Status indicator with response time
            sites_text += "{} **{}** - {:.0f}ms | Uptime: {:.1f}%\\n".format(
                status_emoji, name, avg_time, uptime_24h
            )
            
            # Current status info
            if monitor.is_up:
                if monitor.last_up_time:
                    uptime_duration = time.time() - monitor.last_up_time
                    sites_text += "   └─ Up for {}\\n".format(
                        str(timedelta(seconds=int(uptime_duration)))
                    )
            else:
                if monitor.downtime_start:
                    downtime_duration = time.time() - monitor.downtime_start
                    sites_text += "   └─ Down for {}\\n".format(
                        str(timedelta(seconds=int(downtime_duration)))
                    )
                    
        if sites_text:
            embed.add_field(
                name="📡 Monitored Websites",
                value=sites_text,
                inline=False
            )
            
        # Footer with legend
        embed.set_footer(text="🟢 = Online | 🔴 = Offline | ms = Response time | Uptime = Last 24h")
        
        return embed
        
    @tasks.loop(seconds=30)
    async def check_websites(self):
        """Main monitoring loop"""
        if not self.monitors:
            return
            
        for monitor in self.monitors.values():
            # Check if it's time to check this website
            if (monitor.last_checked and 
                time.time() - monitor.last_checked < monitor.check_interval):
                continue
                
            await self.check_single_website(monitor)
            
    async def check_single_website(self, monitor: WebsiteMonitor):
        """Check a single website"""
        try:
            start_time = time.time()
            headers = {
                "User-Agent": "UptimeMonitorBot/1.0 (+https://github.com/yourrepo)"
            }
            
            response = requests.get(
                monitor.url, 
                timeout=monitor.timeout,
                headers=headers,
                allow_redirects=True
            )
            response_time = (time.time() - start_time) * 1000  # Convert to ms
            
            # Check if status code indicates success
            is_up = 200 <= response.status_code < 400
            
            if is_up:
                await self.handle_website_up(monitor, response_time)
                monitor.last_check_succeeded = True
            else:
                await self.handle_website_down(
                    monitor, 
                    "HTTP {}".format(response.status_code)
                )
                monitor.last_check_succeeded = False
                
        except requests.exceptions.Timeout:
            await self.handle_website_down(monitor, "Timeout")
            monitor.last_check_succeeded = False
        except requests.exceptions.ConnectionError:
            await self.handle_website_down(monitor, "Connection Error")
            monitor.last_check_succeeded = False
        except requests.exceptions.RequestException as e:
            await self.handle_website_down(monitor, str(e))
            monitor.last_check_succeeded = False
        except Exception as e:
            await self.handle_website_down(monitor, "Unexpected Error: {}".format(str(e)))
            monitor.last_check_succeeded = False
            
    async def handle_website_up(self, monitor: WebsiteMonitor, response_time: float):
        """Handle website coming back up or staying up"""
        monitor.last_checked = time.time()
        monitor.response_times.append(response_time)
        
        # If it was down, notify about recovery
        if not monitor.is_up:
            monitor.is_up = True
            monitor.last_up_time = time.time()
            
            # Calculate downtime duration
            downtime_duration = 0
            if monitor.downtime_start:
                downtime_duration = time.time() - monitor.downtime_start
                monitor.total_downtime += downtime_duration
                
            monitor.consecutive_failures = 0
            
            # Send recovery alert
            embed = discord.Embed(
                title="✅ Website Recovered",
                description="**{}** is back online!".format(monitor.name),
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(
                name="URL", 
                value=monitor.url, 
                inline=False
            )
            embed.add_field(
                name="Response Time", 
                value="{:.2f}ms".format(response_time), 
                inline=True
            )
            if downtime_duration > 0:
                embed.add_field(
                    name="Downtime Duration", 
                    value=str(timedelta(seconds=int(downtime_duration))), 
                    inline=True
                )
            
            await self.send_alert(embed)
            
        monitor.add_status_record(True, response_time)
        
    async def handle_website_down(self, monitor: WebsiteMonitor, error: str):
        """Handle website being down"""
        monitor.last_checked = time.time()
        monitor.consecutive_failures += 1
        
        # Only alert if crossing threshold
        if monitor.is_up and monitor.consecutive_failures >= monitor.failure_threshold:
            monitor.is_up = False
            monitor.downtime_start = time.time()
            
            # Send downtime alert with @everyone
            embed = discord.Embed(
                title="🚨 Website Down",
                description="**{}** is unreachable!".format(monitor.name),
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.add_field(name="URL", value=monitor.url, inline=False)
            embed.add_field(name="Error", value=error, inline=False)
            embed.add_field(
                name="Failures", 
                value="{}/{}".format(monitor.consecutive_failures, monitor.failure_threshold), 
                inline=True
            )
            
            # Add @everyone mention in content
            content = "{} **ALERT:** Website monitor detected downtime!".format(self.alert_role)
            await self.send_alert(embed, content=content)
            
        monitor.add_status_record(False, error=error)
        
    async def send_alert(self, embed: discord.Embed, content: str = None):
        """Send alert to all configured channels"""
        # You can configure specific channels
        for guild in self.guilds:
            # Find first channel where bot has send permissions
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    try:
                        await channel.send(content=content, embed=embed)
                    except discord.Forbidden:
                        pass
                    break  # Only send to first valid channel per guild

# --- INTERACTIVE BUTTONS FOR DASHBOARD ---
class DashboardView(ui.View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)  # Persistent view
        self.bot = bot_instance
        
    @ui.button(label="🔄 Refresh Now", style=ButtonStyle.primary, custom_id="refresh_dashboard")
    async def refresh_button(self, interaction: discord.Interaction, button: ui.Button):
        """Manually refresh dashboard"""
        embed = self.bot.generate_dashboard_embed()
        await interaction.response.edit_message(embed=embed)
        
    @ui.button(label="⏸️ Pause Monitoring", style=ButtonStyle.red, custom_id="pause_monitoring")
    async def pause_button(self, interaction: discord.Interaction, button: ui.Button):
        """Toggle monitoring on/off"""
        if self.bot.check_websites.is_running():
            self.bot.check_websites.stop()
            button.label = "▶️ Resume Monitoring"
            button.style = ButtonStyle.green
        else:
            self.bot.check_websites.start()
            button.label = "⏸️ Pause Monitoring"
            button.style = ButtonStyle.red
            
        embed = self.bot.generate_dashboard_embed()
        await interaction.response.edit_message(embed=embed, view=self)

# Slash Commands
bot = UptimeBot()

@bot.tree.command(name="monitor", description="Website monitoring commands")
@app_commands.describe(
    action="Action to perform",
    url="Website URL to monitor",
    name="Name for this monitor",
    interval="Check interval in seconds (default: 60)",
    timeout="Request timeout in seconds (default: 10)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="list", value="list"),
    app_commands.Choice(name="status", value="status"),
    app_commands.Choice(name="dashboard", value="dashboard"),
])
async def monitor_command(
    interaction: discord.Interaction,
    action: str,
    url: Optional[str] = None,
    name: Optional[str] = None,
    interval: Optional[int] = 60,
    timeout: Optional[int] = 10
):
    """Main monitor control command"""
    
    if action == "add":
        if not url or not name:
            await interaction.response.send_message(
                "❌ Please provide both URL and name!", 
                ephemeral=True
            )
            return
            
        # Validate URL
        try:
            parsed = urllib.parse.urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("Invalid URL")
        except:
            await interaction.response.send_message(
                "❌ Invalid URL format! Use: https://example.com", 
                ephemeral=True
            )
            return
            
        # Check if name exists
        if name in bot.monitors:
            await interaction.response.send_message(
                "❌ A monitor with this name already exists!", 
                ephemeral=True
            )
            return
            
        # Create monitor
        monitor = WebsiteMonitor(name, url, interval, timeout)
        bot.monitors[name] = monitor
        bot.save_monitors()
        
        # Test the website immediately
        await interaction.response.send_message(
            "⏳ Adding monitor for **{}**... testing URL...".format(name), 
            ephemeral=True
        )
        
        await bot.check_single_website(monitor)
        
        if monitor.last_check_succeeded:
            await interaction.followup.send(
                "✅ Monitor added! **{}** is responding correctly.\\n"
                "Checks every {} seconds with {}s timeout.".format(
                    name, interval, timeout
                ),
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "⚠️ Monitor added but **{}** appears to be down.\\n"
                "Bot will continue monitoring and alert when it comes back up.".format(name),
                ephemeral=True
            )
            
    elif action == "remove":
        if not name:
            await interaction.response.send_message(
                "❌ Please provide monitor name to remove!", 
                ephemeral=True
            )
            return
            
        if name not in bot.monitors:
            await interaction.response.send_message(
                "❌ No monitor named '{}' found!".format(name), 
                ephemeral=True
            )
            return
            
        del bot.monitors[name]
        bot.save_monitors()
        
        await interaction.response.send_message(
            "✅ Monitor **{}** removed successfully!".format(name), 
            ephemeral=True
        )
        
    elif action == "list":
        if not bot.monitors:
            await interaction.response.send_message(
                "📭 No monitors configured. Add one with `/monitor add`!", 
                ephemeral=True
            )
            return
            
        embed = discord.Embed(
            title="📊 Monitored Websites",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        for name, monitor in bot.monitors.items():
            status = "🟢 UP" if monitor.is_up else "🔴 DOWN"
            embed.add_field(
                name="{} {}".format(status, name),
                value="URL: {}\\nInterval: {}s".format(monitor.url, monitor.check_interval),
                inline=False
            )
            
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    elif action == "status":
        if not name:
            await interaction.response.send_message(
                "❌ Please provide monitor name!", 
                ephemeral=True
            )
            return
            
        if name not in bot.monitors:
            await interaction.response.send_message(
                "❌ No monitor named '{}' found!".format(name), 
                ephemeral=True
            )
            return
            
        monitor = bot.monitors[name]
        
        embed = discord.Embed(
            title="📈 Status: {}".format(monitor.name),
            color=discord.Color.green() if monitor.is_up else discord.Color.red(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="URL", value=monitor.url, inline=False)
        embed.add_field(
            name="Status", 
            value="🟢 Online" if monitor.is_up else "🔴 Offline", 
            inline=True
        )
        embed.add_field(
            name="Avg Response Time", 
            value="{:.2f}ms".format(monitor.get_avg_response_time()), 
            inline=True
        )
        embed.add_field(
            name="Check Interval", 
            value="{} seconds".format(monitor.check_interval), 
            inline=True
        )
        
        # Uptime percentage
        uptime_pct = monitor.get_uptime_percentage(24)
        embed.add_field(
            name="24h Uptime", 
            value="{:.1f}%".format(uptime_pct), 
            inline=True
        )
        
        if monitor.is_up and monitor.last_up_time:
            embed.add_field(
                name="Uptime Duration", 
                value=str(timedelta(seconds=int(time.time() - monitor.last_up_time))), 
                inline=False
            )
        elif not monitor.is_up and monitor.downtime_start:
            embed.add_field(
                name="Downtime Duration", 
                value=str(timedelta(seconds=int(time.time() - monitor.downtime_start))), 
                inline=False
            )
            
        # Recent history
        if monitor.status_history:
            recent = monitor.status_history[-5:]
            history_text = ""
            for record in recent:
                ts = datetime.fromisoformat(record["timestamp"])
                status = "🟢" if record["status"] else "🔴"
                history_text += "{} {}\\n".format(status, ts.strftime("%H:%M:%S"))
            embed.add_field(name="Recent History", value=history_text, inline=False)
            
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    elif action == "dashboard":
        # Create dashboard in current channel
        embed = bot.generate_dashboard_embed()
        view = DashboardView(bot)
        
        await interaction.response.send_message(
            "📊 Creating dashboard...", 
            ephemeral=True
        )
        
        message = await interaction.channel.send(embed=embed, view=view)
        
        # Store dashboard message ID for auto-refresh
        if interaction.guild:
            bot.dashboard_messages[str(interaction.guild.id)] = {
                "channel_id": interaction.channel.id,
                "message_id": message.id
            }
            bot.save_dashboard_messages()
            
        await interaction.followup.send(
            "✅ Dashboard created and will auto-refresh every 5 seconds!",
            ephemeral=True
        )

@bot.tree.command(name="stop_monitor", description="Stop all monitoring")
async def stop_monitor(interaction: discord.Interaction):
    """Stop the monitoring loop"""
    bot.check_websites.stop()
    await interaction.response.send_message(
        "⏹️ Monitoring stopped. Use `/start_monitor` to resume.", 
        ephemeral=True
    )

@bot.tree.command(name="start_monitor", description="Start monitoring loop")
async def start_monitor(interaction: discord.Interaction):
    """Start the monitoring loop"""
    if bot.check_websites.is_running():
        await interaction.response.send_message(
            "▶️ Monitoring is already running!", 
            ephemeral=True
        )
        return
        
    bot.check_websites.start()
    await interaction.response.send_message(
        "✅ Monitoring started!", 
        ephemeral=True
    )

@bot.tree.command(name="cleardashboard", description="Remove the auto-refreshing dashboard")
async def clear_dashboard(interaction: discord.Interaction):
    """Remove dashboard tracking"""
    if str(interaction.guild.id) in bot.dashboard_messages:
        del bot.dashboard_messages[str(interaction.guild.id)]
        bot.save_dashboard_messages()
        await interaction.response.send_message(
            "✅ Dashboard auto-refresh stopped. Delete the message manually.", 
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "❌ No active dashboard found for this server.", 
            ephemeral=True
        )

# Run the bot
if __name__ == "__main__":
    import os
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN environment variable not set!")
        print("   Set it with: export DISCORD_TOKEN='your-token-here'")
        exit(1)
        
    bot.run(TOKEN)
'''

# --- 4. STARTUP ENGINE ---
def run_bot():
    # Setup new loop for this specific background thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Passing 'globals()' ensures functions can see each other
    exec(RAW_CODE, globals())

if FIRST_RUN:
    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()
    st.success("🚀 Bot launched for the first time!")
else:
    st.info("ℹ️ Bot is already running in the background.")

# Show a small clock so the user knows the page is "alive"
st.divider()
st.caption("Last page refresh: {}".format(time.strftime("%H:%M:%S")))
