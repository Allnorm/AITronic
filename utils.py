import asyncio
import configparser
import json
import logging
import os
import sys
import time
import traceback
import base64
from dataclasses import dataclass
from importlib import reload
from typing import Optional

from aiogram import types, exceptions

CHAT_CONFIG_TEMPLATE = {
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
    'markdown_filter': True,
    'split_paragraphs': False,
    'reply_to_quotes': True,
    'show_used_tokens': True,
    'allow_config_everyone': False,
    'max_answer_len': 2000,
    'summarizer_limit': 12000,
    'summariser_prompt': 'Create a short summary of the text previously discussed with the user.',
    'prefill_prompt': None,
    'prefill_mode': 'assistant'
}

MANDATORY_PARAMS = ('api_key', 'model')
PRIVATE_PARAMS = ('api_key', 'system_prompt', 'base_url', 'prefill_prompt')
BOOL_PARAMS = ('vision', 'stream_mode', 'markdown_enable', 'markdown_filter', 'allow_config_everyone',
               'split_paragraphs', 'reply_to_quotes', 'show_used_tokens')
INT_PARAMS = ('attempts', 'threads_limit', 'max_answer_len', 'summarizer_limit')


class IncorrectConfig(Exception):
    pass


@dataclass
class ConfigModeChat:
    chat_id: int
    start_time: int


class ConfigData:
    def __init__(self):

        self.config_mode_chats: dict[int, ConfigModeChat] = {}
        self.chat_config_template = CHAT_CONFIG_TEMPLATE

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
                self.disable_confai = self.bool_init(config["Bot"]["disable-confai"])
                if self.bool_init(config["Bot"]["use-json-template"]):
                    self.json_template_init()
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
        config.set("Bot", "use-json-template", "true")
        config.set("Bot", "disable-confai", "false")
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
            raise TypeError(f'incorrect bool parameter "{var}"')

    def json_template_init(self):
        try:
            with open("template.json", "r", encoding='utf-8') as json_file:
                json_template: dict = json.loads(json_file.read())
        except FileNotFoundError:
            logging.error(f'File "template.json" was not found. The default chat settings template will be loaded.')
            return
        except Exception as e:
            logging.error(f'Error reading file "template.json". '
                          f'The default chat settings template will be loaded.\n{e}')
            logging.error(traceback.format_exc())
            return

        if json_template.keys() != CHAT_CONFIG_TEMPLATE.keys():
            logging.error('The keys in the loaded JSON template do not match the keys in the sample template. '
                          'The default chat settings template will be loaded.')
            return

        try:
            for name, value in json_template.items():
                config_validator(name, value)
        except IncorrectConfig as e:
            logging.error(f'The loaded JSON template is invalid: {e}. '
                          f'The default chat settings template will be loaded.')
            return

        self.chat_config_template = json_template
        logging.info('The JSON settings template has been successfully loaded.')


class InlineWorker:

    __inlines_dict = {}

    async def auto_remove_old(self):
        while True:
            for key, value in self.__inlines_dict.copy().items():
                if value[0] + 86400 < int(time.time()):
                    self.__inlines_dict.pop(key)
            await asyncio.sleep(3600)

    def add(self, unique_id, text):
        self.__inlines_dict.update({unique_id: [int(time.time()), text]})

    def get(self, unique_id):
        if self.__inlines_dict.get(unique_id):
            return self.__inlines_dict.get(unique_id)[1]
        return None

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

def config_validator(name, value) -> dict:
    name_replace = name.replace('_', '-')
    if name == 'vendor' and value not in ('openai', 'anthropic'):
        raise IncorrectConfig('"vendor" может быть только "openai" или "anthropic".')
    if name == 'prefill_mode' and value not in ('assistant', 'pre-user', 'post-user'):
        raise IncorrectConfig('"prefill_mode" может быть только "assistant", "pre-user" или "post-user".')
    elif name in BOOL_PARAMS:
        if isinstance(value, bool):
            pass
        elif value.lower() == "false":
            value = False
        elif value.lower() == "true":
            value = True
        else:
            raise IncorrectConfig(f'"{name_replace}" может иметь значения только "true" или "false".')
    elif name == 'temperature':
        try:
            if isinstance(value, str):
                value = float(value.replace(",", "."))
        except ValueError:
            raise IncorrectConfig('"temperature" не является числом с плавающей запятой.')
        if not 0 <= value <= 2:
            raise IncorrectConfig('"temperature" имеет недопустимое значение (допускается от 0 до 2, включая дробные).')
    elif name in INT_PARAMS:
        try:
            if isinstance(value, str):
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


async def send_message(message, bot, text: str, markdown_filter, parse_mode=None, reply=False):
    thread_id = message.message_thread_id if message.is_topic_message else None
    if markdown_filter and not parse_mode:
        text = text.replace('*', '').replace('`', '')
    try:
        if reply:
            await message.reply(text, allow_sending_without_reply=True, parse_mode=parse_mode)
        else:
            await bot.send_message(message.chat.id, text, thread_id, parse_mode=parse_mode)
    except exceptions.TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            logging.warning("Telegram could not parse markdown in message, it will be sent without formatting")
            await send_message(message, bot, text, markdown_filter, parse_mode=None, reply=reply)
        elif "text must be non-empty" in str(e) or 'message text is empty' in str(e):
            logging.warning(f"Failed to send empty message in chat! Message content: {text}")
        else:
            logging.error(traceback.format_exc())


async def edit_inline_message(old_txt, service_txt, inline_message_id, full_debug,
                              bot, markdown_filter=None, parse_mode=None, new_txt=''):
    if parse_mode:
        service_txt = f'_{service_txt}_'
    if new_txt and markdown_filter and not parse_mode:
        new_txt = new_txt.replace('*', '').replace('`', '')
    try:
        await bot.edit_message_text(f"{old_txt}\n\n{service_txt}{new_txt}",
                                    inline_message_id=inline_message_id, parse_mode=parse_mode)
    except Exception as e:
        if "can't parse entities" in str(e):
            await edit_inline_message(old_txt, service_txt.replace('_', ""), inline_message_id,
                                      full_debug, bot, markdown_filter, None, new_txt)
        else:
            logging.error(f'Error sending inline message: {e}')
            if full_debug:
                logging.error(traceback.format_exc())
            return


def token_counter_formatter(answer, total_tokens, input_tokens, output_tokens):
    if not (answer or total_tokens or input_tokens):
        return f'{answer}\n\n---\n⚠️ Счётчик токенов и суммарайзер не работают на этом API.'
    if input_tokens and output_tokens:
        in_and_out = f' ({input_tokens} запрос, {output_tokens} ответ)'
    elif input_tokens:
        in_and_out = f' ({input_tokens} запрос)'
    elif output_tokens:
        in_and_out = f' ({output_tokens} ответ)'
    else:
        in_and_out = ""
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    return f'{answer}\n\n---\n💰 {total_tokens} токенов чата использовано{in_and_out}'


def get_current_params(chat_config, accept_show_privates=False):
    answer = "<blockquote expandable>"
    for key, value in chat_config.items():
        if value is None:
            value_text = "не установлен"
        elif key in PRIVATE_PARAMS and not accept_show_privates:
            value_text = "установлен, скрыт"
        elif key == 'api_key':
            if len(value) > 10:
                value_text = value[:3] + '*' * (len(value) - 6) + value[-3:]
            else:
                value_text = '*' * len(value)
        elif isinstance(value, bool):
            value_text = str(value).lower()
        else:
            value_text = value
        result_str = html_fix(f'* {key.replace("_", "-")}: {value_text}')
        if key in MANDATORY_PARAMS:
            result_str = f'<b>{result_str}</b>'
        if key in PRIVATE_PARAMS:
            result_str = f'<i>{result_str}</i>'
        answer += result_str + '\n'
    answer = answer.rstrip()
    answer += ("</blockquote>\nЕсли параметр выделен <b>жирным</b>, то он является обязательным, "
               "и без него запуск диалога с LLM невозможен.\nЕсли параметр выделен <i>курсивом</i>, "
               "то он является непубличным. Значение непубличных параметров можно посмотреть в "
               "режиме настройки чата в ЛС с ботом с помощью команды /confai.")
    return answer

def formatted_timer(timer_in_second):
    if timer_in_second <= 0:
        return "0c."
    elif timer_in_second < 60:
        return time.strftime("%Sс.", time.gmtime(timer_in_second))
    elif timer_in_second < 3600:
        return time.strftime("%Mм. и %Sс.", time.gmtime(timer_in_second))
    elif timer_in_second < 86400:
        return time.strftime("%Hч., %Mм. и %Sс.", time.gmtime(timer_in_second))
    else:
        days = timer_in_second // 86400
        timer_in_second = timer_in_second - days * 86400
        return str(days) + " дн., " + time.strftime("%Hч., %Mм. и %Sс.", time.gmtime(timer_in_second))
    # return datetime.datetime.fromtimestamp(timer_in_second).strftime("%d.%m.%Y в %H:%M:%S")