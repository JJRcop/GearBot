import re
import datetime

from disnake import MessageType
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from Bot import TheRealGearBot
from Util import GearbotLogging
from database.DatabaseConnector import LoggedMessage, LoggedAttachment
from collections import namedtuple

batch = dict()
recent_list = set()
previous_list = set()
last_flush = datetime.datetime.now()
fakeLoggedMessage = namedtuple("BufferedMessage", "messageid content author channel server type pinned attachments")

violation_regex = re.compile("duplicate key value violates unique constraint .* DETAIL: Key \(id\)=\((\d)\) already exists.*")

async def insert_message(message):
    # if message.id not in recent_list and message.id not in previous_list:
    message_type = message.type
    if message_type == MessageType.default:
        message_type = None
    else:
        if not isinstance(message_type, int):
                message_type = message_type.value
    #     m = fakeLoggedMessage(messageid=message.id, content=message.content,
    #                           author=message.author.id,
    #                           channel=message.channel.id, server=message.guild.id,
    #                           type=message_type, pinned=message.pinned, attachments=[LoggedAttachment(id=a.id, name=a.filename,
    #                                                                                        isimage=(a.width is not None or a.width is 0),
    #                                                                                        message_id=message.id) for a in message.attachments])
    #     batch[message.id] = m
    #
    #     recent_list.add(message.id)
    #     if len(batch) >= 1000:
    #         asyncio.create_task(flush(force=True))

    try:
        async with in_transaction():
            is_reply = message.reference is not None and message.reference.channel_id == message.channel.id
            logged = await LoggedMessage.create(messageid=message.id, content=message.content.replace('\x00', ''),
                                        author=message.author.id,
                                        channel=message.channel.id, server=message.guild.id,
                                        type=message_type, pinned=message.pinned,
                                                reply_to=message.reference.message_id if is_reply else None)
        for a in message.attachments:
            await LoggedAttachment.create(id=a.id, name=a.filename,
                                          isimage=(a.width is not None or a.width == 0),
                                          message=logged)

    except IntegrityError:
        return message
    return message



async def flush(force=False):
    try:
        if force or (datetime.datetime.now() - last_flush).total_seconds() > 4 * 60:
            await do_flush()
    except Exception as e:
        await TheRealGearBot.handle_exception("Message flushing", None, e)


async def do_flush():
    global batch, recent_list, previous_list, last_flush

    mine = batch
    batch = dict()
    previous_list = recent_list
    recent_list = set()

    excluded = set()
    while len(excluded) < len(mine):
        try:
            to_insert = set()
            to_insert_attachements = set()
            for message in mine.values():
                if message.messageid in excluded:
                    continue
                to_insert.add(LoggedMessage(messageid=message.messageid, content=message.content,
                                            author=message.author,
                                            channel=message.channel, server=message.server,
                                            type=message.type, pinned=message.pinned))
                for a in message.attachments:
                    if a.id not in excluded:
                        to_insert_attachements.add(a)

            async with in_transaction():
                await LoggedMessage.bulk_create(to_insert)
                await LoggedAttachment.bulk_create(to_insert_attachements)
            last_flush = datetime.now()
            return
        except IntegrityError as e:
            match = re.match(violation_regex, str(e))
            if match is not None:
                excluded.add(int(match.group(1)))
                GearbotLogging.log_key(f"Failed to propagate, duplicate {int(match.group(1))}")
            else:
                raise e


def get_messages_in_range(channel_id, first_id, last_id=None):
    if last_id is not None:
        return [message for message in batch.values() if message.channel == channel_id and message.messageid >= first_id and message.messageid <= last_id]
    else:
        return [message for message in batch.values() if message.channel == channel_id and message.messageid == first_id]

def get_messages_for_channel(channel_id):
    return [message for message in batch.values() if message.channel == channel_id]

def get_messages_for_user_in_guild(user_id, guild_id):
    return [message for message in batch.values() if message.server == guild_id and message.author == user_id]
