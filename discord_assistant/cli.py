from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from pydantic_settings import BaseSettings, SettingsConfigDict

from discord_assistant.client import list_members, send_dm


class DiscordSettings(BaseSettings):
    discord_bot_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


def add_discord_command(subparsers: argparse._SubParsersAction) -> None:
    discord = subparsers.add_parser("discord", help="Send direct messages through a Discord bot")
    commands = discord.add_subparsers(dest="discord_command", required=True)
    add_send_command(commands)
    add_members_command(commands)


def add_send_command(subparsers: argparse._SubParsersAction) -> None:
    send = subparsers.add_parser("send", help="Send a direct message to a Discord user")
    send.add_argument("--user-id", required=True, type=int, help="Discord user ID (enable Developer Mode to copy IDs)")
    message = send.add_mutually_exclusive_group()
    message.add_argument("--message", help="Message text")
    message.add_argument("--message-file", help="UTF-8 text file to use as the message; use - to read stdin")
    send.add_argument("--file", dest="files", action="append", default=[], help="File to attach (repeat for multiple files)")


def add_members_command(subparsers: argparse._SubParsersAction) -> None:
    members = subparsers.add_parser("members", help="List members of a server with their Discord IDs")
    members.add_argument("--guild-id", required=True, type=int, help="Discord server ID")
    members.add_argument("--contains", help="Only show names/usernames containing this text")


def run_discord_command(args: argparse.Namespace) -> None:
    token = DiscordSettings().discord_bot_token.strip()
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is not set. Add your bot token to the environment or .env file.")

    if args.discord_command == "members":
        try:
            members = asyncio.run(list_members(token, args.guild_id))
        except Exception as exc:
            import discord

            if isinstance(exc, discord.DiscordException):
                raise SystemExit(f"Discord error: {exc}") from exc
            raise SystemExit(str(exc)) from exc
        query = (args.contains or "").casefold()
        matching_members = [member for member in members if query in member.display_name.casefold() or query in member.name.casefold()]
        for member in sorted(matching_members, key=lambda item: (item.display_name.casefold(), item.name.casefold(), item.id)):
            print(f"{member.display_name}\t@{member.name}\t{member.id}")
        print(f"{len(matching_members)} member(s) shown")
        return

    content = args.message
    if args.message_file:
        if args.message_file == "-":
            content = sys.stdin.read()
        else:
            try:
                content = Path(args.message_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise SystemExit(f"Could not read message file {args.message_file}: {exc}") from exc

    if not content and not args.files:
        raise SystemExit("Provide message text, a message file, or at least one --file attachment.")
    if content and len(content) > 2000:
        raise SystemExit("Discord messages can contain at most 2,000 characters. Split the message or attach it as a file.")
    for file_path in args.files:
        if not Path(file_path).is_file():
            raise SystemExit(f"Attachment file not found: {file_path}")

    try:
        print(asyncio.run(send_dm(token, args.user_id, content, args.files)))
    except Exception as exc:
        import discord

        if isinstance(exc, discord.DiscordException):
            raise SystemExit(f"Discord error: {exc}") from exc
        raise


def main() -> None:
    parser = argparse.ArgumentParser(prog="discord-assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_send_command(subparsers)
    add_members_command(subparsers)
    args = parser.parse_args()
    if args.command == "send":
        run_discord_command(args)
    elif args.command == "members":
        args.discord_command = "members"
        run_discord_command(args)


if __name__ == "__main__":
    main()
