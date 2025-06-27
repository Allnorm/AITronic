import uuid

import aiogram.exceptions
import asyncio
import json
import logging
import time
import traceback

from aiogram import types, Bot, Dispatcher, exceptions
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from aiogram.utils.keyboard import InlineKeyboardBuilder

import ai_core
import sql_worker
import utils
from utils import IncorrectConfig

config = utils.ConfigData()
bot = Bot(token=config.token)
dp = Dispatcher()
sql_helper = sql_worker.SqlWorker()
inline_worker = utils.InlineWorker()
version = '1.2.2'

dialogs = {}
chats_queue = {}

@dp.message(Command("start"))
async def start(message: types.Message):
    if not await utils.check_whitelist(message, config):
        return

    if dialogs.get(message.chat.id) is None:
        try:
            dialogs.update({message.chat.id:
                                ai_core.Dialog(message.chat.id, config, sql_helper, config.chat_config_template)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return
    chat_config = dialogs.get(message.chat.id).chat_config

    answer = (f"Привет!\nЗдесь вы можете проверить ваши настройки, "
              f"чтобы начать работу с выбранной LLM:\n{utils.get_current_params(chat_config)}")
    try:
        await message.reply(answer, parse_mode='html', disable_web_page_preview=True)
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
            dialogs.update({message.chat.id:
                                ai_core.Dialog(message.chat.id, config, sql_helper, config.chat_config_template)})
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

    answer = ("Чтобы настроить бота для публичного чата, если вы администратор или allow-config-everyone включен:\n"
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
              "но команды /confai edit и /confai done там не используются.\n"
              "Вы можете сохранять настройки чата как шаблон или загружать их из шаблона. "
              "Более подробная информация об этой возможности доступна с помощью команды /template.\n"
              "Для сброса диалога введите команду /reset.")
    await message.reply(answer)

@dp.message(Command("confai"))
async def confai(message: types.Message):

    if config.disable_confai:
        return

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
            dialogs.update({msg_chat_id:
                                ai_core.Dialog(msg_chat_id, config, sql_helper, config.chat_config_template)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return
    chat_config = dialogs.get(msg_chat_id).chat_config

    param_name = utils.extract_arg(message.text, 1)
    if private_messages and not config_mode:
        chat_name = "личных сообщений"
    else:
        try:
            chat_name = "чата " + (await bot.get_chat(msg_chat_id)).title
        except aiogram.exceptions.TelegramForbiddenError:
            await message.reply(f'Ошибка получения имени чата - бот был заблокирован в данном чате.')
            return

    if param_name is None:
        answer = (f"Здесь вы можете проверить ваши настройки для {utils.html_fix(chat_name)}, "
                  f"чтобы начать работу с выбранной LLM:\n"
                  f"{utils.get_current_params(chat_config, private_messages)}\n"
                  f"Подробная информация по настройке - в команде /help")
        if config_mode and (chat_matches or private_messages):
            exit_timer = utils.formatted_timer(
                config.config_mode_timer.get(message.from_user.id) + 300 - int(time.time()))
            answer += f"\n\n⏳ До выхода из режима конфигурации осталось {exit_timer}"

        keyboard_list = []
        for key, value in chat_config.items():
            if isinstance(value, bool):
                param_status = '✅' if value else '❌'
                button = InlineKeyboardButton(text=f'{param_status} {key.replace("_", "-")}',
                                              callback_data=f'cai_{msg_chat_id}_{key.replace("_", "-")}_{value}')
                keyboard_list.append(button)
        try:
            await message.reply(answer, parse_mode='html', disable_web_page_preview=True,
                                reply_markup=InlineKeyboardBuilder().row(*keyboard_list, width=2).as_markup())
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка выполнения команды: {e}")
        return

    admin_statuses = ('administrator', 'creator')
    if not any([private_messages and not config_mode,
                (await bot.get_chat_member(msg_chat_id, message.from_user.id)).status in admin_statuses,
                chat_config.get('allow_config_everyone')]):
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
        elif param_name.replace("-", "_") in utils.PRIVATE_PARAMS:
            await message.reply(f"Настраивать приватные параметры разрешено только в ЛС бота.")
            return

    timer_text = ''
    if config_mode:
        exit_timer = utils.formatted_timer(config.config_mode_timer.get(message.from_user.id) + 300 - int(time.time()))
        timer_text = f"\n\n⏳ До выхода из режима конфигурации осталось {exit_timer}"

    if param_name == 'reset':
        reset_param_name = utils.extract_arg(message.text, 2)
        if reset_param_name:
            if reset_param_name.replace("-", "_") in chat_config:
                if config.chat_config_template.keys() != chat_config.keys():
                    await message.reply("Структура параметров чата не совпадает со структурой по умолчанию "
                                        "(это могло произойти после обновления бота или повреждения данных в БД).\n"
                                        f"Требуется сбросить настройки чата командой /confai reset.{timer_text}")
                    return
                chat_config.update({reset_param_name.replace("-", "_"):
                                        config.chat_config_template.get(reset_param_name.replace("-", "_"))})
                reset_param_name = f"параметра {reset_param_name} "
            else:
                await message.reply(f"Параметр {reset_param_name} не найден в списке параметров.{timer_text}")
        else:
            chat_config = config.chat_config_template
            reset_param_name = ""

        try:
            dialogs.get(msg_chat_id).set_chat_config(sql_helper, chat_config,
                                                     msg_chat_id, reset_param_name.replace('-', "_"))
            await message.reply(f'Настройки {reset_param_name}для {chat_name} успешно сброшены!{timer_text}')
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка выполнения команды: {e}{timer_text}")
        return

    if config.chat_config_template.keys() != chat_config.keys():
        await message.reply("Структура параметров чата не совпадает со структурой по умолчанию "
                            "(это могло произойти после обновления бота или повреждения данных в БД).\n"
                            f"Требуется сбросить настройки чата командой /confai reset.{timer_text}")
        return

    if param_name.replace("-", "_") not in chat_config:
        await message.reply(f"Данный параметр не найден в списке настраиваемых параметров.{timer_text}")
        return

    try:
        param_value = message.text.split(" ", maxsplit=2)[2]
    except IndexError:
        await message.reply(f'Значение аргумента "{param_name}" пустое!{timer_text}')
        return

    try:
        chat_config.update(utils.config_validator(param_name.replace("-", "_"), param_value))
    except utils.IncorrectConfig as e:
        await message.reply(f'Некорректный аргумент: {e}{timer_text}')
        return

    try:
        dialogs.get(msg_chat_id).set_chat_config(sql_helper, chat_config, msg_chat_id, param_name.replace("-", "_"))
        await message.reply(f'Успешно обновлён параметр {param_name} для {chat_name}{timer_text}')
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.reply(f"Ошибка выполнения команды: {e}{timer_text}")
        return


@dp.message(Command("template"))
async def template_(message: types.Message):

    if config.disable_confai or not await utils.check_whitelist(message, config):
        return

    if dialogs.get(message.chat.id) is None:
        try:
            dialogs.update({message.chat.id:
                                ai_core.Dialog(message.chat.id, config, sql_helper, config.chat_config_template)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return

    chat_config = dialogs.get(message.chat.id).chat_config

    try:
        current_templates = sql_helper.get_templates(message.chat.id)
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.reply(f"Ошибка в работе бота: {e}")
        return

    command = utils.extract_arg(message.text, 1)
    if not command:
        if not current_templates:
            templates_text = "\n\nВ данном чате сейчас нет сохранённых шаблонов."
        else:
            try:
                templates_text = '\n\n<b>Список сохранённых шаблонов:</b>'
                for template in current_templates:
                    templates_text += f'\n<i>* {utils.html_fix(template[1])}</i>'
            except Exception as e:
                logging.error(traceback.format_exc())
                templates_text = f"\n\nНе удалось получить список шаблонов чата: {e}"
        await message.reply('Команда "template" позволяет сохранить актуальную конфигурацию для чата, чтобы позже '
                            'загрузить её.\nВведите команду:\n/template add (имя шаблона) для сохранения шаблона;\n'
                            '/template rewrite (имя шаблона) для перезаписи шаблона;\n/template load для загрузки '
                            'шаблона;\n/template remove для удаления шаблона.\n'
                            f'Можно добавить не более 10 шаблонов на один чат.'
                            f'{templates_text}', parse_mode='html')
        return
    elif command in ('add', 'rewrite'):
        if len(current_templates) > 10 and command == 'add':
            await message.reply(f'Можно добавить не более 10 шаблонов!')
            return
        try:
            template_name = message.text.split(" ", maxsplit=2)[2]
        except IndexError:
            await message.reply(f'Имя шаблона пустое!')
            return
        if len(template_name) > 32:
            await message.reply(f'Название шаблона слишком длинное (более 32-х символов)!')
            return
        for template in current_templates:
            if template[1] == template_name:
                if command == 'rewrite':
                    try:
                        sql_helper.delete_template(message.chat.id, template_name)
                        sql_helper.write_template(message.chat.id, template_name, chat_config)
                        await message.reply(f"Шаблон {template_name} успешно перезаписан.")
                    except Exception as e:
                        logging.error(traceback.format_exc())
                        await message.reply(f"Ошибка в работе бота: {e}")
                else:
                    await message.reply(f'Шаблон с таким именем уже существует!')
                return
        if command == 'rewrite':
            await message.reply(f"Шаблон {template_name} не найден в списке шаблонов!")
            return
        try:
            sql_helper.write_template(message.chat.id, template_name, chat_config)
            await message.reply(f"Шаблон {template_name} успешно добавлен.")
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
        return
    elif command in ('load', 'remove'):
        command_list = {'load': 'загрузки', 'remove': 'удаления'}
        command_text = command_list.get(command, '')
        keyboard_list = []
        try:
            if not current_templates:
                await message.reply(f"В этом чате нет созданных шаблонов.")
                return
            for template in current_templates:
                button = InlineKeyboardButton(text=template[1],
                                              callback_data=f't_{command}_{template[1]}')
                keyboard_list.append([button])
            await message.reply(f"Выберите шаблон для {command_text}:",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_list))
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
        return
    else:
        await message.reply(f"Данный аргумент команды /template не найден!")


@dp.callback_query(lambda call: call.data[:len('t_load')] == 't_load')
async def template_button(callback: types.CallbackQuery):

    if config.disable_confai:
        await bot.answer_callback_query(callback.id, "Механизм ConfAI отключен на уровне бота!")
        return

    message = callback.message
    try:
        template_name = callback.data.split('_', maxsplit=2)[2]
        template = sql_helper.get_templates(callback.message.chat.id, template_name)
        if not template:
            await message.edit_text(f"Шаблон {template_name} не найден в БД!")
            return
        new_config = json.loads(template[0][2])
        if new_config.keys() != config.chat_config_template.keys():
            await message.edit_text(f"Шаблон {template_name} устарел или повреждён (ключи не совпадают с "
                                    f"конфигурацией по умолчанию). Требуется удалить или перезаписать шаблон.")
            return
        try:
            for name, value in new_config.items():
                utils.config_validator(name, value)
        except utils.IncorrectConfig as e:
            await message.edit_text(f"Шаблон {template_name} имеет некорректные значения "
                                    f"в параметрах: {e} Требуется удалить или перезаписать шаблон.")
            return
        if dialogs.get(message.chat.id) is None:
            dialogs.update({message.chat.id:
                                ai_core.Dialog(message.chat.id, config, sql_helper, config.chat_config_template)})
        dialogs.get(message.chat.id).set_chat_config(sql_helper, new_config, message.chat.id)
        await message.edit_text(f"Шаблон {template_name} успешно применён для данного чата.")
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.edit_text(f"Ошибка в работе бота: {e}")
        return


@dp.callback_query(lambda call: call.data[:len('t_remove')] == 't_remove')
async def template_button(callback: types.CallbackQuery):

    if config.disable_confai:
        await bot.answer_callback_query(callback.id, "Механизм ConfAI отключен на уровне бота!")
        return

    message = callback.message
    try:
        template_name = callback.data.split('_', maxsplit=2)[2]
        template = sql_helper.get_templates(callback.message.chat.id, template_name)
        if not template:
            await message.edit_text(f"Шаблон {template_name} не найден в БД!")
            return
        sql_helper.delete_template(callback.message.chat.id, template_name)
        await message.edit_text(f"Шаблон {template_name} успешно удалён.")
    except Exception as e:
        logging.error(traceback.format_exc())
        await message.edit_text(f"Ошибка в работе бота: {e}")
        return


@dp.callback_query(lambda call: call.data[:len('cai')] == 'cai')
async def confai_bool(callback: types.CallbackQuery):

    if config.disable_confai:
        await bot.answer_callback_query(callback.id, "Механизм ConfAI отключен на уровне бота!")
        return

    message = callback.message
    reply_markup = message.reply_markup
    button_data = callback.data.split('_')
    button_chat_id = button_data[1]
    button_param_name = button_data[2].replace('-', '_')
    button_param_value = button_data[3]

    private_messages = message.chat.id == callback.from_user.id
    if (config.config_mode_timer.get(callback.from_user.id) and
            config.config_mode_timer.get(callback.from_user.id) + 300 < int(time.time())):
        config.config_mode_timer.pop(callback.from_user.id)
        config.config_mode_chats.pop(callback.from_user.id)

    msg_chat_id = config.config_mode_chats.get(callback.from_user.id)
    if button_chat_id != str(msg_chat_id):
        if button_chat_id == str(message.chat.id) and private_messages:
            msg_chat_id = message.chat.id
        elif msg_chat_id:
            await bot.answer_callback_query(callback.id, "Вы уже настраиваете другой чат!")
            return
        else:
            await bot.answer_callback_query(callback.id, "Вы не находитесь в режиме конфигурации чата!")
            return

    if dialogs.get(msg_chat_id) is None:
        try:
            dialogs.update({msg_chat_id: ai_core.Dialog(msg_chat_id, config,
                                                           sql_helper, config.chat_config_template)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return

    chat_config = dialogs.get(msg_chat_id).chat_config

    if not any([private_messages and button_chat_id == str(message.chat.id),
                (await bot.get_chat_member(msg_chat_id, callback.from_user.id)).status
                in ('administrator', 'creator'),
                chat_config.get('allow_config_everyone')]):
        await bot.answer_callback_query(callback.id, "Вы не являетесь администратором чата!")
        return

    if private_messages and button_chat_id == str(message.chat.id):
        chat_name = "личных сообщений"
    else:
        try:
            chat_name = (await bot.get_chat(msg_chat_id)).title
        except aiogram.exceptions.TelegramForbiddenError:
            await bot.answer_callback_query(
                callback.id, f'Ошибка получения имени чата - бот был заблокирован в настраиваемом чате.',
            show_alert=True)
            return

    if config.chat_config_template.get(button_param_name) is None:
        await bot.answer_callback_query(
            callback.id, f'Параметр "{button_param_name.replace("_", "-")}" не найден в списке '
                         f'доступных параметров!', show_alert=True)
        return

    try:
        button_param_value = utils.config_validator(button_param_name, button_param_value)[button_param_name]
    except IncorrectConfig as e:
        await bot.answer_callback_query(
            callback.id, f'Некорректное значение параметра '
                         f'{button_param_name.replace("_", "-")}: {e}', show_alert=True)
        return

    if chat_config.get(button_param_name) != button_param_value:
        await bot.answer_callback_query(
            callback.id, f'Параметр {button_param_name.replace("_", "-")} для {chat_name} уже '
                         f'имеет значение {not button_param_value}.', show_alert=True)
    else:
        button_param_value = not button_param_value
        chat_config.update({button_param_name: button_param_value})
        try:
            dialogs.get(msg_chat_id).set_chat_config(sql_helper, chat_config, msg_chat_id, button_param_name)
            await bot.answer_callback_query(
                callback.id, f'Значение параметра {button_param_name.replace("_", "-")} '
                             f'для {chat_name} установлено на {button_param_value}.', show_alert=True)
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return


    for list_ in reply_markup.inline_keyboard:
        for button in list_:
            if button.callback_data == callback.data:
                button.callback_data = (f'cai_{button_chat_id}_{button_param_name.replace("_", "-")}'
                                        f'_{button_param_value}')
                if '❌' in button.text:
                    button.text = button.text.replace('❌', '✅')
                else:
                    button.text = button.text.replace('✅', '❌')
    await bot.edit_message_reply_markup(chat_id=message.chat.id,
                                        message_id=message.message_id,
                                        reply_markup=reply_markup)

@dp.callback_query(lambda call: call.data[:len('inline')] == 'inline')
async def inline_button(callback: types.CallbackQuery):

    inline_message_id = callback.inline_message_id
    user_id = callback.from_user.id

    if config.whitelist and str(user_id) not in config.whitelist:
        await utils.edit_inline_message('', f"❗Ваш User ID не найден в вайтлисте бота. "
                                            f"Вы не можете его использовать.",
                                        inline_message_id, config.full_debug, bot, 'markdown')
        return

    msg_txt = inline_worker.get(callback.data.split('_', maxsplit=1)[1])
    if not msg_txt:
        await utils.edit_inline_message('', f"❗Текст сообщения не найден в оперативной памяти бота.",
                                        inline_message_id, config.full_debug, bot, 'markdown')
        return

    username = callback.from_user.first_name
    if callback.from_user.last_name:
        username += f' {callback.from_user.last_name}'

    if dialogs.get(user_id) is None:
        try:
            dialogs.update({user_id: ai_core.Dialog(user_id, config, sql_helper, config.chat_config_template)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await utils.edit_inline_message(msg_txt, f"❗Ошибка в работе бота: {e}", inline_message_id,
                                            config.full_debug, bot, 'markdown')
            return

    chat_config = dialogs.get(user_id).chat_config
    broken_params = []
    for key, value in chat_config.items():
        if key in utils.MANDATORY_PARAMS and value is None:
            broken_params.append(key.replace("_", "-"))
    if broken_params:
        service_txt = ("❗ В личных сообщениях бота не заполнены следующие параметры: "
                       + ", ".join(broken_params) + ". Бот не будет работать.")
        await utils.edit_inline_message(msg_txt, service_txt, inline_message_id,
                                        config.full_debug, bot, 'markdown')
        return

    parse_mode = 'markdown' if chat_config.get('markdown_enable') else None

    logging.info(f"User {username} send an inline request to LLM")
    await utils.edit_inline_message(msg_txt, f'⌛ Генерация ответа...', inline_message_id,
                                    config.full_debug, bot, parse_mode)

    try:
        answer = await dialogs.get(user_id).get_answer_inline(username, msg_txt)
    except ai_core.ApiRequestException as e:
        await utils.edit_inline_message(msg_txt, f'❌ Ошибка в работе бота: {e}', inline_message_id,
                                        config.full_debug, bot, parse_mode)
        return

    await utils.edit_inline_message(msg_txt, 'Ответ:', inline_message_id,
                                    config.full_debug, bot, parse_mode, answer)


@dp.message(lambda message: utils.check_names(message, config))
async def handler(message: types.Message):

    if not await utils.check_whitelist(message, config):
        return

    if dialogs.get(message.chat.id) is None:
        try:
            dialogs.update({message.chat.id:
                                ai_core.Dialog(message.chat.id, config, sql_helper, config.chat_config_template)})
        except Exception as e:
            logging.error(traceback.format_exc())
            await message.reply(f"Ошибка в работе бота: {e}")
            return

    chat_config = dialogs.get(message.chat.id).chat_config
    broken_params = []
    for key, value in chat_config.items():
        if key in utils.MANDATORY_PARAMS and value is None:
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

    reply_msg_text = None
    if message.reply_to_message:
        if message.quote:
            reply_msg_text = message.quote.text
        elif any([message.reply_to_message.text, message.reply_to_message.caption,
                  utils.get_poll_text(message.reply_to_message)]):
            reply_msg_text = (message.reply_to_message.text
                              or message.reply_to_message.caption
                              or utils.get_poll_text(message.reply_to_message))
    reply_msg = {"name": utils.username_parser(message.reply_to_message),
                 "text": reply_msg_text} if reply_msg_text else None

    logging.info(f"User {utils.username_parser(message)} send a request to LLM")
    parse_mode = 'markdown' if chat_config.get('markdown_enable') else None

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action='typing')
    except exceptions.TelegramBadRequest as e:
        logging.error(f'Error sending message to chat {message.chat.id}\n{e}')
        return

    try:
        answer = await dialogs.get(message.chat.id).get_answer(message, reply_msg, photo_base64)
    except ai_core.ApiRequestException as e:
        await message.reply(f"Ошибка в работе бота: {e}")
        return
    answer = utils.answer_parser(answer, chat_config)
    chat_queue = chats_queue.get(message.chat.id)
    if not chat_queue:
        chats_queue.update({message.chat.id: asyncio.Lock()})
        chat_queue = chats_queue.get(message.chat.id)

    locked = chat_queue.locked()
    await chat_queue.acquire()
    if locked:
        await asyncio.sleep(3)
    await utils.send_message(message, bot, answer[0], parse=parse_mode, reply=True)
    for paragraph in answer[1::]:
        try:
            await bot.send_chat_action(chat_id=message.chat.id, action='typing')
        except exceptions.TelegramBadRequest:
            pass
        await asyncio.sleep(3)
        await utils.send_message(message, bot, paragraph, parse=parse_mode)
    chat_queue.release()


@dp.inline_query(lambda inline_query: inline_query.query != '')
async def inline(inline_query: types.inline_query.InlineQuery):
    unique_id = ''
    if config.whitelist and str(inline_query.from_user.id) not in config.whitelist:
        n_w_text = 'Ваш User ID не найден в вайтлисте бота.'
        query_result = InlineQueryResultArticle(
            id=str(inline_query.from_user.id),
            title=n_w_text,
            input_message_content=InputTextMessageContent(
                message_text=f'_❗{n_w_text} Вы не можете его использовать._',
                parse_mode='markdown'),
            description=n_w_text
        )
    elif len(inline_query.query) == 255:
        query_result = InlineQueryResultArticle(
            id="msg_too_long",
            title="Сообщение слишком длинное",
            input_message_content=InputTextMessageContent(
                message_text=f'_❗ Сообщение слишком длинное (≥255 символов).\n'
                             f'При отправке в чат оно бы обрезалось._',
                parse_mode='markdown'
            ),
            description="Длина сообщения больше 255 символов"
        )
    else:
        unique_id = str(uuid.uuid4())
        query_result = InlineQueryResultArticle(
            id=unique_id,
            title='Спросить нейросеть',
            input_message_content=InputTextMessageContent(message_text=inline_query.query),
            description=inline_query.query,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text='Сгенерировать ответ',
                callback_data=f'inline_{unique_id}')]])
        )

    if unique_id:
        inline_worker.add(unique_id, inline_query.query)
    await bot.answer_inline_query(inline_query.id, results=[query_result])


@dp.message(Command("version"))
async def version_(message: types.Message):
    if await utils.check_whitelist(message, config):
        await message.reply(f'AITronic, версия {version}\n'
                            'Дата сборки: 27.06.2025\n'
                            'Created by Allnorm aka DvadCat')


async def main():
    get_me = await bot.get_me()
    config.my_id = get_me.id
    config.my_username = f"@{get_me.username}"
    logging.info(f"###AITRONIC v{version} LAUNCHED SUCCESSFULLY###")
    asyncio.create_task(inline_worker.auto_remove_old())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
