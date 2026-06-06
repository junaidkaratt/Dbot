import discord
from discord.ext import commands
import json
import os
import logging
import httpx
from datetime import datetime
from grammar_router import check_grammar
from keep_alive import keep_alive

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load config
with open("config.json", "r") as f:
    config = json.load(f)
for key, value in config.items():
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        config[key] = os.environ.get(env_name)

BOT_TOKEN = config["bot_token"]
KICK_ROLES = [role.lower() for role in config["kick_roles"]]
KICK_REASON = config.get("kick_reason", "You were assigned a restricted role.")
LOG_CHANNEL_ID = config.get("log_channel_id")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ── Fact Check ─────────────────────────────────────────────────────────────────

async def fact_check(statement: str) -> str:
    """
    Returns 'Yes', 'No', or 'This is not a statement.' by asking the LLM.
    Tries providers in priority order just like the grammar router.
    """
    from llm_providers import PROVIDERS

    prompt = (
        f'You are a fact-checking assistant.\n\n'
        f'Rules:\n'
        f'- If the input is a verifiable factual statement, reply with only "Yes" (if true) or "No" (if false).\n'
        f'- If it is a question, opinion, command, or not a factual statement, reply with only "This is not a statement."\n'
        f'- Output only one of these three exact strings. Nothing else.\n\n'
        f'Input: "{statement}"'
    )

    for provider in sorted(PROVIDERS, key=lambda p: p["priority"]):
        api_key = os.environ.get(provider["env_key"])
        if not api_key:
            continue
        call = provider["call"]
        try:
            if call["type"] == "openai_compat":
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        call["base_url"] + "/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                            **call.get("extra_headers", {}),
                        },
                        json={
                            "model": call["model"],
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 10,
                            "temperature": 0,
                        },
                        timeout=15,
                    )
                    response.raise_for_status()
                    raw = response.json()["choices"][0]["message"]["content"].strip()

            elif call["type"] == "gemini":
                from google import genai
                client = genai.Client(api_key=api_key)
                resp = client.models.generate_content(model=call["model"], contents=prompt)
                raw = resp.text.strip()

            else:
                continue

            logger.info(f"✅ Fact check via {provider['name']}: {raw}")
            return raw

        except Exception as e:
            logger.warning(f"⏭️  Fact check {provider['name']} failed: {e}")
            continue

    return "❌ All providers failed. Please try again later."


# ── Message Router ─────────────────────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        # Strip the bot mention to get clean content
        clean = message.content \
            .replace(f"<@{bot.user.id}>", "") \
            .replace(f"<@!{bot.user.id}>", "") \
            .strip()

        # ── Mode 1: Fact check — @bot #statement ──
        if clean.startswith("#"):
            statement = clean[1:].strip()
            if not statement:
                await message.reply("⚠️ Please provide a statement after `#`.\nExample: `@bot #the earth is round`")
                return
            logger.info(f"🔍 Fact check by {message.author.name}: '{statement[:60]}'")
            async with message.channel.typing():
                answer = await fact_check(statement)
            await message.reply(answer)
            return

        # ── Mode 2: Grammar check — @bot as a reply ──
        if message.reference is not None:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                original_text = ref_msg.content.strip()

                if not original_text:
                    await message.reply("⚠️ The message you replied to has no text to check.")
                    return

                logger.info(f"📝 Grammar check by {message.author.name}: '{original_text[:60]}'")

                async with message.channel.typing():
                    result, provider_used = await check_grammar(original_text)

                if result is None:
                    await message.reply(
                        "❌ All grammar providers are currently unavailable or rate-limited. "
                        "Please try again later."
                    )
                    return

                footer = f"Requested by {message.author.display_name} • via {provider_used}"

                if result.get("is_correct"):
                    embed = discord.Embed(
                        title="✅ Grammar Looks Good!",
                        description=f"**Original message:**\n> {original_text}",
                        color=discord.Color.green(),
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(name="📋 Verdict", value=result.get("explanation", "No errors found."), inline=False)
                    embed.set_footer(text=footer)
                else:
                    embed = discord.Embed(title="✏️ Grammar Corrected", color=discord.Color.orange(), timestamp=datetime.utcnow())
                    embed.add_field(name="❌ Original", value=f"> {original_text}", inline=False)
                    embed.add_field(name="✅ Corrected", value=f"> {result.get('corrected_text', 'N/A')}", inline=False)
                    embed.add_field(name="📋 What was fixed", value=result.get("explanation", "Grammar corrections applied."), inline=False)
                    embed.set_footer(text=footer)

                await message.reply(embed=embed)

            except discord.NotFound:
                await message.reply("⚠️ Couldn't find the original message.")
            except Exception as e:
                logger.error(f"Error in grammar check: {e}")
                await message.reply("❌ An unexpected error occurred.")

    await bot.process_commands(message)


# ── Role Kick Logic ────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info(f"✅ Bot online as {bot.user} (ID: {bot.user.id})")
    logger.info(f"👀 Restricted roles: {config['kick_roles']}")
    await bot.change_presence(activity=None)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    added_roles = set(after.roles) - set(before.roles)
    for role in added_roles:
        if role.name.lower() in KICK_ROLES:
            logger.info(f"⚠️ Restricted role '{role.name}' assigned to {after.name}")
            try:
                try:
                    await after.send(
                        f"👋 You have been kicked from **{after.guild.name}**.\n"
                        f"**Reason:** {KICK_REASON}\n\n"
                        f"You were assigned the role **{role.name}**, which is not permitted."
                    )
                except discord.Forbidden:
                    logger.warning(f"Could not DM {after.name}")
                await after.kick(reason=f"Restricted role: {role.name}")
                logger.info(f"🚪 Kicked {after.name}")
                await send_log(guild=after.guild, member=after, role=role, action="Kicked")
            except discord.Forbidden:
                logger.error(f"❌ No permission to kick {after.name}")
            except discord.HTTPException as e:
                logger.error(f"❌ HTTP error kicking {after.name}: {e}")


async def send_log(guild, member, role, action):
    if not LOG_CHANNEL_ID:
        return
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return
    embed = discord.Embed(title=f"🚪 Member {action}", color=discord.Color.red(), timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="Trigger Role", value=role.name, inline=True)
    embed.add_field(name="Action", value=action, inline=True)
    embed.set_footer(text=f"Server: {guild.name}")
    embed.set_thumbnail(url=member.display_avatar.url)
    await channel.send(embed=embed)


# ── Admin Commands ─────────────────────────────────────────────────────────────

@bot.command(name="listroles")
@commands.has_permissions(administrator=True)
async def list_roles(ctx):
    roles = config["kick_roles"]
    if not roles:
        await ctx.send("⚠️ No restricted roles configured.")
        return
    embed = discord.Embed(
        title="🚫 Restricted Roles",
        description="\n".join(f"• `{r}`" for r in roles),
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)


@bot.command(name="addrole")
@commands.has_permissions(administrator=True)
async def add_role(ctx, *, role_name: str):
    if role_name.lower() not in [r.lower() for r in config["kick_roles"]]:
        config["kick_roles"].append(role_name)
        KICK_ROLES.append(role_name.lower())
        save_config()
        await ctx.send(f"✅ Role `{role_name}` added.")
        logger.info(f"Admin {ctx.author} added '{role_name}' to kick_roles")
    else:
        await ctx.send(f"⚠️ `{role_name}` is already restricted.")


@bot.command(name="removerole")
@commands.has_permissions(administrator=True)
async def remove_role(ctx, *, role_name: str):
    match = next((r for r in config["kick_roles"] if r.lower() == role_name.lower()), None)
    if match:
        config["kick_roles"].remove(match)
        if role_name.lower() in KICK_ROLES:
            KICK_ROLES.remove(role_name.lower())
        save_config()
        await ctx.send(f"✅ Role `{role_name}` removed.")
        logger.info(f"Admin {ctx.author} removed '{role_name}' from kick_roles")
    else:
        await ctx.send(f"⚠️ `{role_name}` not found.")


@bot.command(name="providers")
@commands.has_permissions(administrator=True)
async def list_providers(ctx):
    from llm_providers import PROVIDERS
    lines = []
    for p in sorted(PROVIDERS, key=lambda x: x["priority"]):
        status = "✅ Key set" if os.environ.get(p["env_key"]) else "❌ No key"
        lines.append(f"`#{p['priority']}` **{p['name']}** — {status}")
    embed = discord.Embed(title="🤖 LLM Providers", description="\n".join(lines), color=discord.Color.blurple())
    embed.set_footer(text="Providers tried in order. First success wins.")
    await ctx.send(embed=embed)


def save_config():
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need **Administrator** permission.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Usage: `!{ctx.command.name} <role name>`")


keep_alive()
bot.run(BOT_TOKEN)