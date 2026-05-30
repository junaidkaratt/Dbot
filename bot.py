import discord
from discord.ext import commands
import json
import os
import logging
from datetime import datetime
from keep_alive import keep_alive

# Setup logging
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     handlers=[
#         logging.FileHandler("bot.log"),
#         logging.StreamHandler()
#     ]
# )
# logger = logging.getLogger(__name__)

# Load config
with open("config.json", "r") as f:
    config = json.load(f)
for key, value in config.items():
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        config[key] = os.environ.get(env_name)

BOT_TOKEN = config["bot_token"]
KICK_ROLES = [role.lower() for role in config["kick_roles"]]  # Role names to watch
KICK_REASON = config.get("kick_reason", "You were assigned a restricted role.")
LOG_CHANNEL_ID = config.get("log_channel_id")  # Optional logging channel

intents = discord.Intents.default()
intents.members = True  # Required to detect role changes

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info(f"✅ Bot is online as {bot.user} (ID: {bot.user.id})")
    logger.info(f"👀 Watching for roles: {config['kick_roles']}")
    await bot.change_presence(activity=None)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Fires whenever a member's roles change."""
    # Find newly added roles
    added_roles = set(after.roles) - set(before.roles)

    for role in added_roles:
        if role.name.lower() in KICK_ROLES:
            logger.info(
                f"⚠️  Restricted role '{role.name}' assigned to "
                f"{after.name}#{after.discriminator} ({after.id}) "
                f"in '{after.guild.name}'"
            )

            try:
                # Notify the user via DM before kicking
                try:
                    dm_message = (
                        f"👋 You have been kicked from **{after.guild.name}**.\n"
                        f"**Reason:** {KICK_REASON}\n\n"
                        f"You were assigned the role **{role.name}**, "
                        f"which is not permitted in this server."
                    )
                    await after.send(dm_message)
                    logger.info(f"📨 DM sent to {after.name}#{after.discriminator}")
                except discord.Forbidden:
                    logger.warning(f"⚠️  Could not DM {after.name} (DMs disabled)")

                # Kick the member
                await after.kick(reason=f"Restricted role assigned: {role.name}")
                logger.info(f"🚪 Kicked {after.name}#{after.discriminator} successfully")

                # Log to a Discord channel if configured
                await send_log(
                    guild=after.guild,
                    member=after,
                    role=role,
                    action="Kicked"
                )

            except discord.Forbidden:
                logger.error(
                    f"❌ Missing permissions to kick {after.name}#{after.discriminator}. "
                    f"Ensure the bot role is above the target member's role."
                )
            except discord.HTTPException as e:
                logger.error(f"❌ HTTP error while kicking {after.name}: {e}")


async def send_log(guild: discord.Guild, member: discord.Member, role: discord.Role, action: str):
    """Send an action log to a designated Discord channel."""
    if not LOG_CHANNEL_ID:
        return

    channel = guild.get_channel(LOG_CHANNEL_ID)
    if not channel:
        logger.warning(f"⚠️  Log channel ID {LOG_CHANNEL_ID} not found in guild.")
        return

    embed = discord.Embed(
        title=f"🚪 Member {action}",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="Trigger Role", value=role.name, inline=True)
    embed.add_field(name="Action", value=action, inline=True)
    embed.set_footer(text=f"Server: {guild.name}")
    embed.set_thumbnail(url=member.display_avatar.url)

    await channel.send(embed=embed)


# --- Admin Commands ---

@bot.command(name="listroles")
@commands.has_permissions(administrator=True)
async def list_roles(ctx):
    """List all roles that will trigger a kick."""
    roles = config["kick_roles"]
    if not roles:
        await ctx.send("⚠️ No restricted roles configured.")
        return
    role_list = "\n".join(f"• `{r}`" for r in roles)
    embed = discord.Embed(
        title="🚫 Restricted Roles",
        description=role_list,
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)


@bot.command(name="addrole")
@commands.has_permissions(administrator=True)
async def add_role(ctx, *, role_name: str):
    """Add a role to the restricted list."""
    if role_name.lower() not in [r.lower() for r in config["kick_roles"]]:
        config["kick_roles"].append(role_name)
        KICK_ROLES.append(role_name.lower())
        save_config()
        await ctx.send(f"✅ Role `{role_name}` added to restricted list.")
        logger.info(f"Admin {ctx.author} added '{role_name}' to kick_roles")
    else:
        await ctx.send(f"⚠️ Role `{role_name}` is already in the restricted list.")


@bot.command(name="removerole")
@commands.has_permissions(administrator=True)
async def remove_role(ctx, *, role_name: str):
    """Remove a role from the restricted list."""
    match = next((r for r in config["kick_roles"] if r.lower() == role_name.lower()), None)
    if match:
        config["kick_roles"].remove(match)
        if role_name.lower() in KICK_ROLES:
            KICK_ROLES.remove(role_name.lower())
        save_config()
        await ctx.send(f"✅ Role `{role_name}` removed from restricted list.")
        logger.info(f"Admin {ctx.author} removed '{role_name}' from kick_roles")
    else:
        await ctx.send(f"⚠️ Role `{role_name}` not found in restricted list.")


def save_config():
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need **Administrator** permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument. Usage: `!{ctx.command.name} <role name>`")


keep_alive()
bot.run(BOT_TOKEN)