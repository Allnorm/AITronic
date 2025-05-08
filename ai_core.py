import asyncio
import json
import logging
import traceback
from typing import Optional

import anthropic
import html2text
import openai

import sql_worker
import utils


class ApiRequestException(Exception):
    pass

class Dialog:

    __chat_config: dict

    def __init__(self, chat_id, global_config, sql_helper: sql_worker.SqlWorker):
        self.__chat_config = json.loads(sql_helper.get_dialog_data(chat_id, utils.init_dict)[1])
        self.summarizer_used = False
        self.threads_semaphore = asyncio.Semaphore(self.__chat_config.get('threads_limit'))
        self.global_config = global_config
        self.sql_helper = sql_helper
        self.chat_id = chat_id
        self.memory_dump = None

        try:
            dialog_data = sql_helper.get_dialog_data(chat_id)
        except Exception as e:
            dialog_data = []
            logging.error("AITronic was unable to read conversation information! Please check your database!")
            logging.error(f"{e}\n{traceback.format_exc()}")
        self.dialog_history = []
        if dialog_data and dialog_data[2]:
            self.dialog_history = json.loads(dialog_data[2])
            # Pictures saved in the database may cause problems when working without Vision
            if not self.__chat_config.get('vision'):
                self.dialog_history = self.cleaning_images(self.dialog_history)
        self.system_prompt = self.__chat_config.get('system_prompt')
        self.client = self.make_client()

    def make_client(self):
        api_key = self.__chat_config.get('api_key')
        base_url = self.__chat_config.get('base_url')
        vendor = self.__chat_config.get('vendor')
        if not api_key:
            return None
        if vendor == 'anthropic':
            return anthropic.Anthropic(api_key=api_key, base_url=base_url)
        else:
            return openai.OpenAI(api_key=api_key, base_url=base_url)

    def reset_dialog(self):
        self.dialog_history = []
        self.sql_helper.dialog_update([], self.chat_id)

    @property
    def chat_config(self):
        return self.__chat_config

    def set_chat_config(self, sql_helper, chat_config, msg_chat_id, param_name=None):
        self.__chat_config = chat_config
        if not param_name:
            self.cleaning_images(self.dialog_history)
            self.client = self.make_client()
        elif param_name == 'vision' and not chat_config.get('vision'):
            self.cleaning_images(self.dialog_history)
        elif param_name in ('vendor', 'api_key', 'base_url'):
            self.client = self.make_client()
        sql_helper.dialog_conf_update(chat_config, msg_chat_id)

    @staticmethod
    def html_parser(exc_text):
        exc_text = str(exc_text)
        if "html>" not in exc_text:
            return exc_text
        text_converter = html2text.HTML2Text()
        # Disable framing of links with the * symbol
        text_converter.ignore_links = True
        return text_converter.handle(exc_text)

    def send_api_request_openai(self, messages):

        if self.__chat_config.get('system_prompt'):
            system = [{"role": "system", "content": self.__chat_config.get('system_prompt')}]
            system.extend(messages)
            messages = system

        if self.__chat_config.get('prefill'):
            messages.append({"role": "assistant", "content": self.__chat_config.get('prefill')})

        completion = 'The "completion" object was not received.'
        try:
            completion = self.client.chat.completions.create(
                model=self.__chat_config.get('model'),
                messages=messages,
                temperature=self.__chat_config.get('temperature'),
                max_tokens=self.__chat_config.get('max_answer_len'),
                stream=False)
            answer = completion.choices[0].message.content
            if not answer or answer.isspace():
                raise ApiRequestException("Empty text result!")
            return (answer, completion.usage.total_tokens,
                    completion.usage.prompt_tokens, completion.usage.completion_tokens)
        except Exception as e:
            logging.error(f"OPENAI API REQUEST ERROR!\n{self.html_parser(e)}")
            if self.global_config.full_debug:
                logging.error(traceback.format_exc())
                logging.error(completion)
            raise ApiRequestException(self.html_parser(e))

    def send_api_request_anthropic(self, messages):

        if self.__chat_config.get('prefill'):
            messages.append({"role": "assistant", "content": self.__chat_config.get('prefill')})

        completion = 'The "completion" object was not received.'
        if not self.__chat_config.get('stream'):
            try:
                completion = self.client.messages.create(
                    model=self.__chat_config.get('model'),
                    messages=messages,
                    system=self.__chat_config.get('system_prompt'),
                    temperature=self.__chat_config.get('temperature'),
                    max_tokens=self.__chat_config.get('max_answer_len'),
                    stream=False
                )
                if "error" in completion.id:
                    logging.error(completion.content[0].text)
                    raise ApiRequestException(completion.content[0].text)
                text = completion.content[0].text
                if not text or text.isspace():
                    raise ApiRequestException("Empty text result, please check your prefill!")
                while text[0] in (" ", "\n"):  # Sometimes Anthropic spits out spaces and line breaks
                    text = text[1::]  # at the beginning of text
                return (text, completion.usage.input_tokens + completion.usage.output_tokens,
                        completion.usage.input_tokens, completion.usage.output_tokens)
            except Exception as e:
                logging.error(f"ANTHROPIC API REQUEST ERROR!\n{self.html_parser(e)}")
                if self.global_config.full_debug:
                    logging.error(traceback.format_exc())
                    logging.error(completion)
                raise ApiRequestException(self.html_parser(e))

        try:
            input_count = 0
            output_count = 0
            text = ""
            with self.client.messages.stream(
                    model=self.__chat_config.get('model'),
                    messages=messages,
                    system=self.__chat_config.get('system_prompt'),
                    temperature=self.__chat_config.get('temperature'),
                    max_tokens=self.__chat_config.get('max_answer_len'),
            ) as stream:
                empty_stream = True
                error = False
                for event in stream:
                    empty_stream = False
                    name = event.__class__.__name__
                    if name == "MessageStartEvent":
                        if event.message.usage:
                            input_count += event.message.usage.input_tokens
                        else:
                            error = True
                    elif name == "ContentBlockDeltaEvent":
                        text += event.delta.text
                    elif name == "MessageDeltaEvent":
                        output_count += event.usage.output_tokens
                    elif name == "Error":
                        logging.error(event.error.message)
                        raise ApiRequestException
                if empty_stream:
                    raise ApiRequestException("Empty stream object, please check your proxy connection!")
                if error:
                    raise ApiRequestException(text)
                if not text or text.isspace():
                    raise ApiRequestException("Empty text result, please check your prefill!")
            while text[0] in (" ", "\n"):
                text = text[1::]
            return text, input_count + output_count, input_count, output_count
        except Exception as e:
            logging.error(f"ANTHROPIC API REQUEST ERROR!\n{self.html_parser(e)}")
            if self.global_config.full_debug:
                logging.error(traceback.format_exc())
                logging.error(completion)
            raise ApiRequestException(self.html_parser(e))

    async def send_api_request(self, messages):
        attempts = self.__chat_config.get('attempts')
        for attempt in range(attempts):
            try:
                if self.__chat_config.get('vendor') == 'anthropic':
                    return await asyncio.get_running_loop().run_in_executor(
                        None, self.send_api_request_anthropic, messages)
                else:
                    return await asyncio.get_running_loop().run_in_executor(
                        None, self.send_api_request_openai, messages)
            except ApiRequestException as e:
                if attempt + 1 == attempts:
                    raise e
                continue
        return None

    def get_image_context(self, photo_base64, prompt):
        if self.__chat_config.get('vendor') == 'anthropic':
            return [
                {"type": "image", "source":
                    {"type": "base64", "media_type": photo_base64['mime'], "data": photo_base64['data']}},
                {"type": "text", "text": prompt}]
        else:
            return [
                {"type": "image_url", "image_url":
                    {"url": f"data:{photo_base64['mime']};base64,{photo_base64['data']}"}},
                {"type": "text", "text": prompt}]

    async def get_answer(self, message, reply_msg: Optional[dict], photo_base64):
        await self.threads_semaphore.acquire()
        username = utils.username_parser(message)
        chat_name = f"{username}'s private messages" if message.chat.title is None else f'chat {message.chat.title}'
        reply_msg_text = ""
        if reply_msg and self.dialog_history:
            if not self.dialog_history[-1]['content'] == reply_msg["text"]:
                reply_msg_text = f'Previous message ({reply_msg["name"]}): "{reply_msg["text"]}"\n'

        msg_txt = message.text or message.caption or utils.get_poll_text(message)
        if msg_txt is None:
            msg_txt = "I sent a sticker" if photo_base64['mime'] == "image/webp" else "I sent a photo"

        main_text = f"Message ({username}): {msg_txt}"
        dialog_buffer = self.dialog_history.copy()
        prompt = f'{reply_msg_text}{main_text}'
        if photo_base64:
            dialog_buffer.append({"role": "user", "content": self.get_image_context(photo_base64, prompt)})
        else:
            dialog_buffer.append({"role": "user", "content": prompt})
        try:
            answer, total_tokens, input_tokens, output_tokens = await self.send_api_request(dialog_buffer)
            if self.global_config.full_debug:
                logging.info(f"--FULL DEBUG INFO FOR API REQUEST--\n\n{self.system_prompt}\n\n{dialog_buffer}"
                             f"\n\n{answer}\n\n--END OF FULL DEBUG INFO FOR API REQUEST--")
        except ApiRequestException as e:
            self.threads_semaphore.release()
            if self.global_config.full_debug:
                logging.info(f"--FULL DEBUG INFO FOR API REQUEST--\n\n{self.system_prompt}\n\n{dialog_buffer}"
                             f"\n\n--END OF FULL DEBUG INFO FOR API REQUEST--")
            raise ApiRequestException(f"Ошибка запроса к LLM: {e}")

        logging.info(f'{total_tokens} tokens counted by the OpenAI API in {chat_name}.')
        prompt = f'{reply_msg_text}{main_text}'
        if photo_base64:
            self.dialog_history.extend([{"role": "user", "content": self.get_image_context(photo_base64, prompt)},
                                        {"role": "assistant", "content": answer}])
        else:
            self.dialog_history.extend([{"role": "user", "content": prompt},
                                        {"role": "assistant", "content": answer}])
        if self.__chat_config.get('vision') and len(self.dialog_history) > 10:
            self.dialog_history = self.cleaning_images(self.dialog_history, last_only=True)
        if total_tokens >= self.__chat_config.get('summarizer_limit') and not self.summarizer_used:
            logging.info(f"The token limit {self.__chat_config.get('summarizer_limit')} for "
                         f"the {chat_name} has been exceeded. Using a lazy summarizer")
            try:
                await self.summarizer(chat_name)
            except ApiRequestException as e:
                message.reply(f"Ошибка суммарайзинга диалога: {e}.\nПросьба проверить логи бота!")

        if self.__chat_config.get('show_used_tokens'):
            answer = utils.token_counter_formatter(answer, total_tokens, input_tokens, output_tokens)
        try:
            self.sql_helper.dialog_update(self.dialog_history, self.chat_id)
        except Exception as e:
            logging.error("AITronic was unable to save conversation information! Please check your database!")
            logging.error(f"{e}\n{traceback.format_exc()}")
            message.reply(f"Ошибка записи ответа нейросети в БД: {e}.\n"
                          f"Контекст разговора будет утрачен после перезапуска бота!")
        self.threads_semaphore.release()
        if self.threads_semaphore._value >= self.__chat_config.get('threads_limit') and self.summarizer_used:
            self.summarizer_used = False
        return answer

    # This code clears the context from old images so that they do not cause problems in operation
    # noinspection PyTypeChecker
    @staticmethod
    def cleaning_images(dialog, last_only=False):

        def cleaner():
            if isinstance(dialog[index]['content'], list):
                for i in dialog[index]['content']:
                    if i['type'] == 'text':
                        dialog[index]['content'] = i['text']

        if last_only:
            for index in range(len(dialog) - 11, -1, -1):
                cleaner()
        else:
            for index in range(len(dialog)):
                cleaner()
        return dialog

    def summarizer_index(self, threshold=None):
        text_len = 0
        for index in range(len(self.dialog_history)):
            if isinstance(self.dialog_history[index]['content'], list):
                for i in self.dialog_history[index]['content']:
                    if i['type'] == 'text':
                        text_len += len(i['text'])
            else:
                text_len += len(self.dialog_history[index]['content'])

            if threshold:
                if text_len >= threshold and self.dialog_history[index]['role'] == "user":
                    return index

        return self.summarizer_index(text_len * 0.7)


    async def summarizer(self, chat_name):
        self.summarizer_used = True
        split = self.summarizer_index()
        compressed_dialogue = self.dialog_history[:split:]
        compressed_dialogue.append({"role": "user", "content": f'{self.__chat_config.get("summariser_prompt")}'})

        # When sending pictures to the summarizer, it does not work correctly, so we delete them
        compressed_dialogue = self.cleaning_images(compressed_dialogue)
        try:
            answer, total_tokens, _, _ = await self.send_api_request(compressed_dialogue)
            if self.global_config.full_debug:
                logging.debug(f"--FULL DEBUG INFO FOR DIALOG COMPRESSING--\n\n{compressed_dialogue}"
                              f"\n\n{answer}\n\n--END OF FULL DEBUG INFO FOR DIALOG COMPRESSING--")
            logging.info(f"{total_tokens} tokens were used to compress the dialogue")
        except ApiRequestException as e:
            if self.global_config.full_debug:
                logging.debug(f"--FULL DEBUG INFO FOR DIALOG COMPRESSING--\n\n{compressed_dialogue}"
                              f"\n\n--END OF FULL DEBUG INFO FOR DIALOG COMPRESSING--")
            logging.error(f"Summarizing failed for {chat_name}!")
            raise e

        logging.info(f"Summarizing completed for {chat_name}, {total_tokens} tokens were used")
        summarizer_data = [{"role": "user", "content": f'{self.__chat_config.get("summariser_prompt")}'},
                           {"role": "assistant", "content": answer}]
        summarizer_data.extend(self.dialog_history[split::])
        self.dialog_history = summarizer_data
