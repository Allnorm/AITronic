import configparser
import logging
import os
import sys
import time
import traceback
import base64
from importlib import reload
from typing import Optional

from aiogram import types, exceptions


init_dict = {
    'api_key': None,
    'system_prompt': None,
    'model': None,
    'vendor': 'openai',
    'base_url': None,
    'vision': False,
    'stream_mode': False,
    'temperature': 0.5,
    'attempts': 7,
    'threads_limit': 10,
    'markdown_enable': True,
    'split_paragraphs': False,
    'reply_to_quotes': True,
    'max_answer_len': 2000,
    'summarizer_limit': 12000,
    'summariser_prompt': 'Create a short summary of the text previously discussed with the user.',
    'prefill_prompt': None
}

mandatory_parameters = ('api_key', 'model')
private_parameters = ('api_key', 'system_prompt', 'base_url')


class IncorrectConfig(Exception):
    pass


class ConfigData:
    def __init__(self):

        self.config_mode_chats = {}
        self.config_mode_timer = {}

        reload(logging)
        logging.basicConfig(
            handlers=[
                logging.FileHandler("logging.log", 'w', 'utf-8'),
                logging.StreamHandler(sys.stdout)
            ],
            force=True,
            level=logging.INFO,
            format='%(asctime)s %(levelname)s: %(message)s',
            datefmt="%d-%m-%Y %H:%M:%S")

        if not os.path.isfile("config.ini"):
            print("Config file isn't found! Trying to remake!")
            self.remake_conf()

        config = configparser.ConfigParser()
        while True:
            try:
                config.read("config.ini")
                self.token = config["Bot"]["token"]
                self.whitelist = config["Bot"]["whitelist-chats"]
                self.tag_phrase = config["Bot"]["tag-phrase"]
                self.full_debug = self.bool_init(config["Bot"]["full-debug"])
                break
            except Exception as e:
                logging.error(str(e))
                logging.error(traceback.format_exc())
                time.sleep(1)
                print("\nInvalid config file! Trying to remake!")
                agreement = "-1"
                while agreement != "y" and agreement != "n" and agreement != "":
                    agreement = input("Do you want to reset your broken config file on defaults? (Y/n): ")
                    agreement = agreement.lower()
                if agreement == "" or agreement == "y":
                    self.remake_conf()
                else:
                    sys.exit(0)

    @staticmethod
    def remake_conf():
        token = ""
        while token == "":
            token = input("Please, write your bot token: ")

        config = configparser.ConfigParser()
        config.add_section("Bot")
        config.set("Bot", "token", token)
        config.set("Bot", "whitelist-chats", "")
        config.set("Bot", "tag-phrase", "gpt")
        config.set("Bot", "full-debug", "false")
        try:
            config.write(open("config.ini", "w"))
            print("New config file was created successful")
        except IOError:
            print("ERR: Bot cannot write new config file and will close")
            logging.error(traceback.format_exc())
            sys.exit(1)

    @staticmethod
    def bool_init(var):
        if var.lower() in ("false", "0"):
            return False
        elif var.lower() in ("true", "1"):
            return True
        else:
            raise TypeError

def username_parser(message, html=False):
    if message.from_user.first_name == "":
        return "DELETED USER"

    if message.from_user.username == "GroupAnonymousBot":
        return "ANONYMOUS ADMIN"

    if message.from_user.last_name is None:
        username = str(message.from_user.first_name)
    else:
        username = str(message.from_user.first_name) + " " + str(message.from_user.last_name)

    if not html:
        return username

    return html_fix(username)

def username_parser_chat_member(chat_member, html=False, need_username=True):
    if chat_member.user.username is None or need_username is False:
        if chat_member.user.last_name is None:
            username = chat_member.user.first_name
        else:
            username = chat_member.user.first_name + " " + chat_member.user.last_name
    else:
        if chat_member.user.last_name is None:
            username = chat_member.user.first_name + " (@" + chat_member.user.username + ")"
        else:
            username = chat_member.user.first_name + " " + chat_member.user.last_name + \
                       " (@" + chat_member.user.username + ")"

    if not html:
        return username

    return html_fix(username)


def html_fix(text):
    """
    Fixes some characters that could cause problems with parse_mode=html
    :param text:
    :return:
    """
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


async def check_whitelist(message: types.Message, config):
    if str(message.chat.id) in config.whitelist or not config.whitelist:
        return True
    chat_name = username_parser(message) if not message.chat.title else message.chat.title
    logging.info(f"Rejected request from chat {chat_name}")
    await message.reply("Данный чат не найден в вайтлисте бота. Бот здесь работать не будет.")
    return False


def extract_arg(text, num):
    try:
        return text.split()[num]
    except (IndexError, AttributeError):
        return None

def config_validator(name, value):
    name_replace = name.replace('_', '-')
    if name == 'vendor':
        if value not in ('openai', 'anthropic'):
            raise IncorrectConfig('"vendor" может быть только "openai" или "anthropic".')
    elif name in ('vision', 'stream_mode', 'markdown_enable', 'split_paragraphs', 'reply_to_quotes'):
        if value.lower() == "false":
            value = False
        elif value.lower() == "true":
            value = True
        else:
            raise IncorrectConfig(f'"{name_replace}" может иметь значения только "true" или "false".')
    elif name == 'temperature':
        try:
            value = float(value.replace(",", "."))
        except ValueError:
            raise IncorrectConfig('"temperature" не является числом с плавающей запятой.')
        if not 0 <= value <= 2:
            raise IncorrectConfig('"temperature" имеет недопустимое значение (допускается от 0 до 2, включая дробные).')
    elif name in ('attempts', 'threads_limit', 'max_answer_len', 'summarizer_limit'):
        try:
            if not value.isdigit():
                raise ValueError
            value = int(value)
        except ValueError:
            raise IncorrectConfig(f'"{name_replace}" не является целым числом.')
    if name == 'attempts' and not 1 <= value <= 10:
        raise IncorrectConfig(f'"{name_replace}" имеет недопустимое значение (допускается от 1 до 10).')
    if name == 'threads_limit' and not 1 <= value <= 10:
        raise IncorrectConfig(f'"{name_replace}" имеет недопустимое значение (допускается от 1 до 10).')
    if name == 'max_answer_len' and value < 50:
        raise IncorrectConfig(f'"{name_replace}" имеет недопустимое значение (допускается от 50).')
    if name == 'summarizer_limit' and value < 1000:
        raise IncorrectConfig(f'"{name_replace}" имеет недопустимое значение (допускается от 1000).')
    return {name: value}


def check_names(message, config):
    """
    The bot will only respond if called by name (if it's public chat)
    :param message:
    :param config:
    :return:
    """

    if not any([message.text, message.caption, message.photo, message.sticker, message.poll]):
        return False
    if message.chat.id == message.from_user.id:
        return True
    if message.reply_to_message:
        if message.reply_to_message.from_user.id == config.my_id:
            return True
    msg_txt = message.text or message.caption
    if msg_txt is None:
        return False
    if msg_txt[:len(config.tag_phrase)] == config.tag_phrase:
        return True
    return False


async def get_image_from_message(message, bot) -> Optional[dict]:
    if not message:
        return None
    elif message.photo:
        byte_file = await bot.download(message.photo[-1].file_id)
        mime = "image/jpeg"
    elif message.sticker:
        byte_file = await bot.download(message.sticker.thumbnail.file_id)
        mime = "image/webp"
    else:
        return None
    # noinspection PyUnresolvedReferences
    return {"data": base64.b64encode(byte_file.getvalue()).decode('utf-8'), "mime": mime}


def get_poll_text(message):
    if not message.poll:
        return None
    poll_text = message.poll.question + "\n\n"
    for option in message.poll.options:
        poll_text += "☑️ " + option.text + "\n"
    return poll_text


def message_len_parser(text, config, fn_list):
    max_len = config.get('max_answer_len')

    while len(text) > max_len:

        parsed = False

        for parse_fn in fn_list:
            for index in range(max_len, 1, -1):
                if parse_fn(text, index):
                    yield text[:index]
                    text = text[index + 1:]
                    parsed = True
                    break
            if parsed:
                break
        if parsed:
            continue
        yield text[:max_len]
        text = text[max_len:]

    yield text


def answer_parser(text, config) -> list:

    def lines_parser(txt, index):
        return txt[index] == "\n"

    def sentences_parser(txt, index):
        return txt[index] == " " and txt[index - 1] in ".!?"

    def space_parser(txt, index):
        return txt[index] == " "

    fn_list = (lines_parser, sentences_parser, space_parser)

    answer = text.split("\n\n") if config.get('split_paragraphs') else [text]
    split_answer = []
    for answer_part in answer:
        split_answer.extend([parsed_txt for parsed_txt in message_len_parser(answer_part, config, fn_list)])
    return split_answer


async def send_message(message, bot, text, parse=None, reply=False):
    thread_id = message.message_thread_id if message.is_topic_message else None
    try:
        if reply:
            await message.reply(text, allow_sending_without_reply=True, parse_mode=parse)
        else:
            await bot.send_message(message.chat.id, text, thread_id, parse_mode=parse)
    except exceptions.TelegramBadRequest as exc:
        if "can't parse entities" in str(exc):
            logging.warning("Telegram could not parse markdown in message, it will be sent without formatting")
            await send_message(message, bot, text, reply=reply)
        elif "text must be non-empty" in str(exc) or 'message text is empty' in str(exc):
            logging.warning(f"Failed to send empty message in chat! Message content: {text}")
        else:
            logging.error(traceback.format_exc())
