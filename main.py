import asyncio
import logging
import time
import traceback

from aiogram import types, Bot, Dispatcher, exceptions
from aiogram.filters.command import Command

import ai_core
import sql_worker
import utils
from ai_core import ApiRequestException

config = utils.ConfigData()
bot = Bot(token=config.token)
dp = Dispatcher()
sql_helper = sql_worker.SqlWorker()

dialogs = {}

def get_current_params(chat_config, accept_show_privates=False):
    answer = "<blockquote expandable>"
    for key, value in chat_config.items():
        if value is None:
            value_text = "не установлен"
        elif key in utils.private_parameters and not accept_show_privates:
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
        result_str = f'* {key.replace("_", "-")}: {value_text}'
        if key in utils.mandatory_parameters:
            result_str = f'<b>{result_str}</b>'
        if key in utils.private_parameters:
            result_str = f'<i>{result_str}</i>'
        answer += result_str + '\n'
    answer = answer.rstrip()
    answer += ("</blockquote>\nЕсли параметр выделен <b>жирным</b>, то он является обязательным, "
               "и без него запуск диалога с LLM невозможен.\nЕсли параметр выделен <i>курсивом</i>, "
               "то он является непубличным. Значение непубличных параметров можно посмотреть в "
               "режиме настройки чата в ЛС с ботом с помощью команды /confai.")
    return answer

@dp.message(Command("start"))
async def start(message: types.Message):
    if not await utils.check_whitelist(message, config):
        return

    if dialogs.get(message.chat.id) is None:
        try:
            dialogs.update({message.chat.id: ai_core.Dialog(message.chat.id, config, sql_helper)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return
    chat_config = dialogs.get(message.chat.id).chat_config

    answer = (f"Привет!\nЗдесь вы можете проверить ваши настройки, "
              f"чтобы начать работу с выбранной LLM:\n{get_current_params(chat_config)}")
    try:
        await message.reply(answer, parse_mode='html')
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.reply(f"Ошибка выполнения команды: {e}")
    return


@dp.message(Command("reset"))
async def confai(message: types.Message):
    if not await utils.check_whitelist(message, config):
        return

    if not dialogs.get(message.chat.id):
        try:
            dialogs.update({message.chat.id: ai_core.Dialog(message.chat.id, config, sql_helper)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return

    dialog = dialogs.get(message.chat.id)
    if not dialog.dialog_history:
        await message.reply(f"У вас нет диалога с ботом!")
        return

    try:
        dialog.reset_dialog()
        await message.reply(f"Контекст диалога успешно сброшен!")
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.reply(f"Ошибка выполнения команды: {e}")

@dp.message(Command("help"))
async def help_(message: types.Message):
    if not await utils.check_whitelist(message, config):
        return

    answer = ("Чтобы настроить бота для публичного чата, если вы администратор:\n"
              "1. Введите команду /confai edit.\n"
              "2. В личных сообщениях бота или в чате (только для не-приватных параметров) "
              "напишите команду /confai (аргумент) (значение аргумента). "
              "Валидацию корректности введённых данных бот будет проводить автоматически.\n"
              "3. Введите команду /confai reset для сброса всех настроек чата "
              "или /confai reset (аргумент) для сброса настроек конкретного параметра.\n"
              "4. Завершите конфигурацию командой /confai done.\n"
              "Режим конфигурации будет автоматически отключен через 5 минут после его активации. Даже если вы "
              "не находитесь в вайтлисте бота, то всё равно можете настроить его в чате таким образом.\n"
              "Для личных сообщений бот настраивается аналогично, "
              "но команды /confai edit и /confai done там не используются.")
    await message.reply(answer)

@dp.message(Command("confai"))
async def confai(message: types.Message):

    private_messages = message.chat.id == message.from_user.id
    if (config.config_mode_timer.get(message.from_user.id) and
            config.config_mode_timer.get(message.from_user.id) + 300 < int(time.time())):
        config.config_mode_timer.pop(message.from_user.id)
        config.config_mode_chats.pop(message.from_user.id)

    config_mode, chat_matches = False, False
    msg_chat_id = config.config_mode_chats.get(message.from_user.id)
    if msg_chat_id:
        config_mode = True
        if msg_chat_id == message.chat.id:
            chat_matches = True
        elif not private_messages:
            msg_chat_id = message.chat.id
    else:
        msg_chat_id = message.chat.id

    if config.whitelist and not str(msg_chat_id) in config.whitelist:
        chat_name = utils.username_parser(message) if not message.chat.title else message.chat.title
        logging.info(f"Rejected request from chat {chat_name}")
        await message.reply("Данный чат не найден в вайтлисте бота. Бот здесь работать не будет.")
        return

    if dialogs.get(msg_chat_id) is None:
        try:
            dialogs.update({msg_chat_id: ai_core.Dialog(msg_chat_id, config, sql_helper)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return
    chat_config = dialogs.get(msg_chat_id).chat_config

    param_name = utils.extract_arg(message.text, 1)
    if private_messages and not config_mode:
        chat_name = "личных сообщений"
    else:
        chat_name = "чата " + (await bot.get_chat(msg_chat_id)).title

    if param_name is None:
        answer = (f"Здесь вы можете проверить ваши настройки для {chat_name}, "
                  f"чтобы начать работу с выбранной LLM:\n{get_current_params(chat_config, private_messages)}\n"
                  f"Подробная информация по настройке - в команде /help")
        try:
            await message.reply(answer, parse_mode='html')
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка выполнения команды: {e}")
        return

    if (message.chat.id != message.from_user.id and
            (await bot.get_chat_member(message.chat.id, message.from_user.id)).status
            not in ('administrator', 'creator')):
        await message.reply("Не-администраторам чата запрещено использовать эту команду с аргументами!")
        return

    if param_name == 'edit':
        if private_messages:
            await message.reply("Использовать эту команду для настройки бота в личных сообщениях не требуется.")
            return
        for key, value in config.config_mode_chats.items():
            if key == message.from_user.id or value == msg_chat_id:
                try:
                    if key == message.from_user.id:
                        text = (f"Вы уже запустили режим конфигурации для чата "
                                f"{(await bot.get_chat(value)).title}!")
                    else:
                        username = utils.username_parser_chat_member(await bot.get_chat_member(value, key))
                        text = (f"Пользователь {username} уже запустил режим конфигурации для чата "
                                f"{(await bot.get_chat(value)).title}! Повторите попытку позже.")
                    await message.reply(text)
                except exceptions.TelegramBadRequest as e:
                    logging.error(traceback.format_exc())
                    await message.reply(f"Ошибка выполнения команды: {e}")
                finally:
                    return
        config.config_mode_timer.update({message.from_user.id: int(time.time())})
        config.config_mode_chats.update({message.from_user.id: msg_chat_id})
        await message.reply(f"Вы успешно запустили режим конфигурации для {chat_name}. "
                            f"У вас есть 5 минут для настройки параметров LLM.")
        return

    if param_name == 'done':
        if not config_mode:
            await message.reply(f"Вы сейчас не находитесь в режиме конфигурации!")
            return
        elif not (private_messages or chat_matches):
            await message.reply(f"Вы можете выйти из режима конфигурации только в ЛС или в конфигурируемом чате!")
            return
        config.config_mode_timer.pop(message.from_user.id)
        config.config_mode_chats.pop(message.from_user.id)
        await message.reply(f"Вы успешно вышли из режима конфигурации.")
        return

    if not private_messages:
        if not config_mode:
            await message.reply("Вы не находитесь в режиме конфигурации.")
            return
        elif not chat_matches:
            await message.reply("В режиме конфигурации вы можете настраивать "
                                "бота только в ЛС или конфигурируемом чате!")
            return
        elif param_name.replace("-", "_") in utils.private_parameters:
            await message.reply(f"Настраивать приватные параметры разрешено только в ЛС бота.")
            return

    if param_name == 'reset':
        reset_param_name = utils.extract_arg(message.text, 2)
        if reset_param_name:
            if chat_config.get(reset_param_name.replace("-", "_")):
                chat_config.update({reset_param_name.replace("-", "_"):
                                        utils.init_dict.get(reset_param_name.replace("-", "_"))})
                reset_param_name = f"параметра {reset_param_name} "
            else:
                await message.reply(f"Параметр {reset_param_name} не найден в списке параметров.")
        else:
            chat_config = utils.init_dict
            reset_param_name = ""

        try:
            dialogs.get(msg_chat_id).set_chat_config(sql_helper, chat_config, msg_chat_id, None)
            await message.reply(f'Настройки {reset_param_name}для {chat_name} успешно сброшены!')
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка выполнения команды: {e}")
        finally:
            return

    if param_name.replace("-", "_") not in list(chat_config.keys()):
        await message.reply(f"Данный параметр не найден в списке настраиваемых параметров.")
        return

    try:
        param_value = message.text.split(" ", maxsplit=2)[2]
    except IndexError:
        await message.reply(f'Значение аргумента "{param_name}" пустое!')
        return

    try:
        chat_config.update(utils.config_validator(param_name.replace("-", "_"), param_value))
    except utils.IncorrectConfig as e:
        await message.reply(f'Некорректный аргумент: {e}')
        return

    try:
        dialogs.get(msg_chat_id).set_chat_config(sql_helper, chat_config, msg_chat_id, param_name.replace("-", "_"))
        await message.reply(f'Успешно обновлён параметр {param_name} для {chat_name}')
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.reply(f"Ошибка выполнения команды: {e}")
        return

@dp.message(lambda message: utils.check_names(message, config))
async def handler(message: types.Message):

    if not await utils.check_whitelist(message, config):
        return

    if dialogs.get(message.chat.id) is None:
        try:
            dialogs.update({message.chat.id: ai_core.Dialog(message.chat.id, config, sql_helper)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return

    chat_config = dialogs.get(message.chat.id).chat_config
    broken_params = []
    for key, value in chat_config.items():
        if key in utils.mandatory_parameters and value is None:
            broken_params.append(key.replace("_", "-"))
    if broken_params:
        await message.reply("Для чата не заполнены следующие параметры: "
                            + ", ".join(broken_params) + ". Бот не будет работать.")
        return

    vision = True if chat_config.get('vision') else False

    if not any([message.text, message.caption, vision]):
        return

    if message.quote and not chat_config.get('reply_to_quotes'):
        return

    photo_base64 = None
    try:
        if vision:
            photo_base64 = (await utils.get_image_from_message(message, bot) or
                            await utils.get_image_from_message(message.reply_to_message, bot))
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.reply(f"Ошибка в работе бота: {e}")
        return

    reply_msg = None
    if message.reply_to_message:
        if message.quote:
            reply_msg = message.quote.text
        elif any([message.reply_to_message.text, message.reply_to_message.caption,
                  utils.get_poll_text(message.reply_to_message)]):
            reply_msg = (message.reply_to_message.text
                         or message.reply_to_message.caption
                         or utils.get_poll_text(message.reply_to_message))
        reply_msg = {"name": utils.username_parser(message.reply_to_message), "text": reply_msg}

    logging.info(f"User {utils.username_parser(message)} send a request to ChatGPT")
    parse_mode = 'markdown' if chat_config.get('markdown_enable') else None
    await bot.send_chat_action(chat_id=message.chat.id, action='typing')
    try:
        answer = await dialogs.get(message.chat.id).get_answer(message, reply_msg, photo_base64)
    except ApiRequestException as e:
        await message.reply(str(e))
        return
    answer = utils.answer_parser(answer, chat_config)
    await utils.send_message(message, bot, answer[0], parse=parse_mode, reply=True)
    for paragraph in answer[1::]:
        await bot.send_chat_action(chat_id=message.chat.id, action='typing')
        await asyncio.sleep(3)
        await utils.send_message(message, bot, paragraph, parse=parse_mode)

async def main():
    get_me = await bot.get_me()
    config.my_id = get_me.id
    config.my_username = f"@{get_me.username}"
    logging.info("###AITRONIC v0.1 alpha LAUNCHED SUCCESSFULLY###")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
