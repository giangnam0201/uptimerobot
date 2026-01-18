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

# --- 3. YOUR CODE (FIXED) ---
RAW_CODE = '''
# uptime_monitor_bot.py
import discord
from discord import app_commands
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
        self.last_check_succeeded = None  # Track immediate check result
        
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

class UptimeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        
        self.monitors: Dict[str, WebsiteMonitor] = {}
        self.alert_role = "@everyone"  # Can be configured
        self.load_monitors()
        
    async def setup_hook(self):
        """Initialize the bot"""
        self.check_websites.start()
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
        
        # Perform the check and wait for result
        await bot.check_single_website(monitor)
        
        # Check the ACTUAL result of the immediate test (not the monitor.is_up flag)
        # last_check_succeeded will be True/False based on the immediate check
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
        # Create comprehensive dashboard
        total_sites = len(bot.monitors)
        if total_sites == 0:
            await interaction.response.send_message(
                "📭 No monitors to display. Add one with `/monitor add`!", 
                ephemeral=True
            )
            return
            
        up_sites = sum(1 for m in bot.monitors.values() if m.is_up)
        down_sites = total_sites - up_sites
        
        embed = discord.Embed(
            title="🎯 Uptime Monitoring Dashboard",
            description="Monitoring **{}** websites".format(total_sites),
            color=discord.Color.green() if down_sites == 0 else discord.Color.red(),
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="Overview",
            value="🟢 Online: **{}**\\n🔴 Offline: **{}**".format(up_sites, down_sites),
            inline=False
        )
        
        # List all sites with status
        sites_text = ""
        for name, monitor in bot.monitors.items():
            status_emoji = "🟢" if monitor.is_up else "🔴"
            avg_time = monitor.get_avg_response_time()
            sites_text += "{} **{}** - {:.0f}ms\\n".format(status_emoji, name, avg_time)
            
        if sites_text:
            embed.add_field(name="All Monitors", value=sites_text, inline=False)
            
        # Calculate overall uptime (simplified)
        if total_sites > 0:
            uptime_pct = (up_sites / total_sites) * 100
            embed.add_field(
                name="System Health",
                value="**{:.1f}%** of services are online".format(uptime_pct),
                inline=False
            )
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
