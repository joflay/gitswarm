from __future__ import annotations

import discord


async def send_dm(token: str, user_id: int, content: str | None, file_paths: list[str]) -> str:
    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        user = await client.fetch_user(user_id)
        channel = await user.create_dm()
        files = [discord.File(file_path) for file_path in file_paths]
        try:
            await channel.send(content=content or None, files=files)
        finally:
            for file in files:
                file.close()
        return f"Message sent to {user} ({user.id})"
    finally:
        await client.close()


async def list_members(token: str, guild_id: int) -> list[discord.Member]:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True
    client = discord.Client(intents=intents)
    members: list[discord.Member] = []
    error: Exception | None = None

    @client.event
    async def on_ready() -> None:
        nonlocal error
        try:
            guild = client.get_guild(guild_id)
            if guild is None:
                raise ValueError(f"Bot is not in server {guild_id}, or the server was not available at startup.")
            async for member in guild.fetch_members(limit=None):
                members.append(member)
        except Exception as exc:
            error = exc
        finally:
            await client.close()

    try:
        await client.start(token)
    finally:
        await client.close()

    if error:
        raise error
    return members
