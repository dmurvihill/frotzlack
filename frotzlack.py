import logging
import os
import re
import time
from ConfigParser import ConfigParser
from logging.handlers import RotatingFileHandler
from multiprocessing.pool import ThreadPool
from Queue import Empty, Queue
from threading import Thread, Lock

import pexpect
from slacksocket import SlackSocket


CRASH_MSG = "Sorry, I seem to have crashed."
LOAD_NOT_IMPL_MSG = "Sorry, loading is not implemented yet."
QUIT_NOT_IMPL_MSG = "Sorry, quitting is not implemented yet."
SERVER_SHUTDOWN_MSG = "Sorry, the server is shutting down now."
SAVE_SUCCESS_MSG = "I saved your game, but I won't be able to load it."
SAVE_FAILURE_MSG = "Sorry, something went wrong and I wasn't able to save your game."


class GameMaster(object):
    """
    Manages user sessions.
    """
    def __init__(self, config):
        #  Slack config
        slack_api_token = config.get('slack', 'api_token')
        self._slack_username = config.get('slack', 'bot_username')
        self._slack = SlackSocket(slack_api_token, translate=True)
        self._slack_events = self._slack.events()
        self._sessions = {}
        self._slack_event_handler = Thread(target=self._handle_slack_events,
                                           name='event_handler')

        #  Frotz config
        self._frotz_binary = config.get('frotz', 'path')
        self._frotz_story_file = config.get('frotz', 'story')

        #  Logging config
        self._logs_dir = config.get('frotzlack', 'logs_dir')
        error_log_path = os.path.join(self._logs_dir, 'frotzlack.log')
        self._global_handler = RotatingFileHandler(error_log_path)
        self._global_handler.setLevel(logging.WARNING)

        #  Other config
        self._admins = config.get('frotzlack', 'admins').split(',')
        self._save_dir = config.get('frotzlack', 'save_dir')

        self._stop_requested = False
        self._slack_event_handler.start()

    def stop_server(self):
        pool = ThreadPool()
        for session in self._sessions.values():
            def polite_stop():
                session.say("Sorry, the server is shutting down now.")
                session.kill()
            pool.apply_async(polite_stop)
        pool.close()
        pool.join()
        self._stop_requested = True

    def join(self):
        for session in self._sessions.values():
            session.join()
        self._slack_event_handler.join()

    def _event_is_game_input(self, event):
        event_attrs = event.event
        is_game_input = 'type' in event_attrs.keys() and \
            event_attrs['type'] == 'message' and \
            'user' in event_attrs and \
            event_attrs['user'] in self._sessions and \
            event_attrs['user'] != self._slack_username and \
            self._slack_username not in event.mentions and \
            event_attrs['channel'] == event_attrs['user']
        return is_game_input

    def _event_is_command(self, event):
        event_attrs = event.event
        return 'type' in event_attrs.keys() and \
               event_attrs['type'] == 'message' and \
               self._slack_username in event.mentions

    def _handle_game_input(self, user, game_input):
        session = self._sessions[user]
        if game_input.strip() == 'save':
            try:
                session.save()
            except IOError as e:
                session.say("Save failed! {}".format(e.message))
            else:
                session.say("I saved your game, but I won't be able to load it.")
        elif game_input.strip() == 'restore':
            session.say("Sorry, I can't load games yet.")
        elif game_input.strip() == 'quit':
            session.say("Sorry, I can't quit the game yet.")
        else:
            print("{} putting input to Slack queue '{}'".format(time.time(), game_input))
            session.put(game_input)

    def _start_session(self, username):
        def send_msg(msg):
            self._slack.send_msg(msg, channel_id=channel_id, confirm=False)

        channel_id = self._slack.get_im_channel(username)['id']

        session_logger = logging.getLogger(username)
        session_logger.setLevel(logging.INFO)
        session_log_path = os.path.join(self._logs_dir, username + '.log')
        session_handler = RotatingFileHandler(session_log_path)
        session_handler.setLevel(logging.INFO)
        session_logger.addHandler(session_handler)
        session_logger.addHandler(self._global_handler)

        save_path = os.path.join(self._save_dir, username)
        if os.path.relpath(save_path).startswith(self._save_dir):
            slack_session = SlackSession(send_msg, channel_id)
            frotz_session = FrotzSession(self._frotz_binary,
                                         self._frotz_story_file,
                                         session_logger)
            self._sessions[username] = Session(slack_session,
                                               frotz_session,
                                               save_path)

    def _reject_command(self, username, command):
        channel = self._slack.get_im_channel(username)
        message = "Sorry, I don't recognize the command `{0}`"
        self._slack.send_msg(message.format(command), channel_id=channel['id'])

    def _handle_slack_events(self):
        while not self._stop_requested:
            event = self._slack_events.next()
            event_attrs = event.event
            if self._event_is_game_input(event):
                msg = event_attrs['text']
                self._handle_game_input(event_attrs['user'], msg)
            elif self._event_is_command(event):
                command = event_attrs['text']
                user = event_attrs['user']
                if 'stop' in command and user in self._admins:
                    self.stop_server()
                elif 'play' in command:
                    if user not in self._sessions:
                        self._start_session(user)
                else:
                    self._reject_command(event_attrs['user'], command)


class SlackSession(object):
    """
    Provides an interface to communicate with a Slack user.
    """
    def __init__(self, message_send, channel_id):
        self._channel_id = channel_id
        self._messages = Queue()
        self._send_msg = message_send

    def put(self, msg):
        self._messages.put(msg)

    def send(self, msg):
        self._send_msg(msg)

    def recv(self):
        msg = self._messages.get(block=False)
        return msg


class FrotzSession(object):
    """
    Provides an interface to communicate with a Frotz process.
    """
    class FrotzError(Exception):
        pass

    save_name_regex = re.compile("Please enter a filename \[.*\]:")
    save_confirm_regex = re.compile("Ok\.")
    save_override_prompt_regex = re.compile("Overwrite existing file\?")

    def __init__(self, frotz_binary, story_file, logger):
        self._frotz_lock = Lock()
        self._send_queue = Queue()
        self._recv_queue = Queue()
        self._stop_requested = False
        self._thread_pool = ThreadPool()
        with self._frotz_lock:
            self._frotz_process = pexpect.spawn(
                ' '.join([frotz_binary, story_file]), timeout=1)
        self._thread_pool.apply_async(self._send_loop)
        self._thread_pool.apply_async(self._recv_loop)
        self._logger = logger

    def send(self, msg):
        self._send_queue.put(msg)

    def recv(self, *args, **kwargs):
        return self._recv_queue.get(*args, **kwargs)

    def save(self, path):
        with self._frotz_lock:
            self._frotz_process.sendline("save")
            self._frotz_process.expect(">save\r\n")

            # Enter file name
            self._frotz_process.expect(FrotzSession.save_name_regex)
            self._frotz_process.sendline(path)

            # Overwrite prompt or success message
            match_index = self._frotz_process.expect([
                FrotzSession.save_confirm_regex,
                FrotzSession.save_override_prompt_regex])

            # If it was an overwrite, confirm
            if match_index == 1:
                self._frotz_process.sendline("yes")
                self._frotz_process.expect(FrotzSession.save_confirm_regex)

    def stop(self):
        self._stop_requested = True

    def join(self):
        self._thread_pool.join()

    def kill(self):
        self.stop()
        self.join()
        self._frotz_process.close(force=True)
        self._logger.info('[terminated]')

    def notify_crash(self, exception):
        self._logger.exception(exception.message)

    def _send_loop(self):
        while not self._stop_requested:
            try:
                msg = self._send_queue.get(block=False)
            except Empty:
                pass
            else:
                with self._frotz_lock:
                    self._logger.info('<<<\t' + msg)
                    self._frotz_process.sendline(msg)
            time.sleep(0)

    def _recv_loop(self):
        while not self._stop_requested:
            with self._frotz_lock:
                try:
                    print('waiting for Frotz...')
                    frotz_line = self._frotz_process.readline()
                except pexpect.TIMEOUT:
                    print('Got nothing.')
                    pass
                else:
                    msg = frotz_line.rstrip()
                    print('{} Got \'{}\''.format(time.time(), msg))
                    self._logger.info('>>>\t' + msg)
                    self._recv_queue.put(msg)
            time.sleep(0)


class Session(object):
    """
    Handles communication between a Slack user and a corresponding
    Frotz process.
    """
    def __init__(self, slack_session, frotz_session, save_path):
        self._stop_requested = False
        self._slack_session = slack_session
        self._frotz_session = frotz_session
        self._save_path = save_path
        self._input_handler = Thread(target=self._handle_input,
                                     name="input_handler")
        self._output_handler = Thread(target=self._handle_output,
                                      name="output_handler")
        self._input_handler.start()
        self._output_handler.start()

    def kill(self, message=SERVER_SHUTDOWN_MSG):
        self._slack_session.send(message)
        self._stop_requested = True
        self._output_handler.join()
        self._input_handler.join()
        self._slack_session.kill()
        self._frotz_session.kill()

    def crash(self, exception):
        self._frotz_session.notify_crash(exception)
        self.kill(message=CRASH_MSG)

    def save(self):
        self._frotz_session.save(self._save_path)

    def say(self, message):
        self._slack_session.send(message)

    def put(self, message):
        self._slack_session.put(message)

    def _handle_input(self):
        while not self._stop_requested:
            try:
                frotz_in = self._slack_session.recv()
                print('{} Sending message \'{}\' to Frotz'.format(time.time(), frotz_in))
                self._frotz_session.send(frotz_in)
            except Empty:
                pass
            except Exception as e:
                self.crash(e)
            time.sleep(0)

    def _handle_output(self):
        while not self._stop_requested:
            try:
                frotz_out = self._frotz_session.recv(block=False)
                print('{} received message \'{}\' from Frotz'.format(time.time(), frotz_out))
                self._slack_session.send(frotz_out)
            except Empty:
                pass
            except Exception as e:
                self.crash(e)
            time.sleep(0)

def main():
    config = ConfigParser()
    config.read('frotzlack.conf')
    gm = GameMaster(config)
    gm.join()

if __name__ == "__main__":
    main()
