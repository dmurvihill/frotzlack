import logging
import os
import re
from ConfigParser import ConfigParser
from logging.handlers import RotatingFileHandler
from multiprocessing.pool import ThreadPool
from Queue import Queue
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

    def _event_is_game_input(self, event):
        event_attrs = event.event
        is_game_input = 'type' in event_attrs.keys() and \
            event_attrs['type'] == 'message' and \
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
        session = self._slack_sessions[user]
        if game_input.strip() == 'save':
            session.save()
            session.say("I saved your game, but I won't be able to load it.")
        elif game_input.strip() == 'quit':
            session.say("Sorry, I can't quit the game yet.")
        else:
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

        slack_session = SlackSession(send_msg, channel_id)
        frotz_session = FrotzSession(self._frotz_binary,
                                     self._frotz_story_file,
                                     session_logger)
        self._slack_sessions[username] = slack_session
        Session(slack_session, frotz_session, username)

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
        return self._messages.get()


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
        self._frotz_process = \
            pexpect.spawn(' '.join([frotz_binary, story_file]))
        self._frotz_lock = Lock()
        self._logger = logger

    def send(self, msg):
        self._frotz_lock.acquire()
        try:
            self._logger.info('<<<\t' + msg)
            self._frotz_process.sendline(msg)
        finally:
            self._frotz_lock.release()

    def recv(self):
        self._frotz_lock.acquire()
        try:
            msg = self._frotz_process.readline().rstrip()
            self._logger.info('>>>\t' + msg)
            return msg
        finally:
            self._frotz_lock.release()

    def save(self, path):
        self._frotz_lock.acquire()
        try:
            self._frotz_process.sendline("save")

            # Enter file name
            self._expect_line([FrotzSession.save_name_regex])
            self._frotz_process.sendline(path)

            # Overwrite prompt or success message
            (matched_pattern, match) = self._expect_line([
                FrotzSession.save_confirm_regex,
                FrotzSession.save_override_prompt_regex])

            # If it was an overwrite, confirm
            if matched_pattern is FrotzSession.save_override_prompt_regex:
                self._frotz_process.sendline("yes")
                self._expect_line([FrotzSession.save_confirm_regex])
        finally:
            self._frotz_lock.release()

    def kill(self):
        self._frotz_process.close(force=True)
        self._logger.info('[terminated]')

    def notify_crash(self, exception):
        self._logger.exception(exception.message)

    def _expect_line(self, expected_patterns):
        actual_line = self._frotz_process.readline().rstrip()
        matched_regex = None
        match = None

        for regex in expected_patterns:
            if match is None:
                matched_regex = regex
                match = regex.match(actual_line)

        if match is None:
            message = "Unexpected line from Frotz: \"{0}\".\n" \
                      "Expected lines:\n\t{1}"
            expected_lines_patterns = map(lambda x: x.pattern,
                                          expected_patterns)
            expected_lines_string = "\n\t".join(expected_lines_patterns)
            message = message.format(actual_line, expected_lines_string)
            raise(FrotzSession.FrotzError(message))

        return matched_regex, match


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

    def notify_input(self, game_input):
        if game_input == 'load':
            self._slack_session.send(LOAD_NOT_IMPL_MSG)
        elif game_input == 'quit':
            self._slack_session.send(QUIT_NOT_IMPL_MSG)
        elif game_input == 'save':
            try:
                self._frotz_session.save(self._save_path)
                self._slack_session.send(SAVE_SUCCESS_MSG)
            except Exception as e:
                self._slack_session.send(SAVE_FAILURE_MSG)
        else:
            self._frotz_session.send(game_input)

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

    def _handle_input(self):
        while not self._stop_requested:
            try:
                frotz_in = self._slack_session.recv()
                self._frotz_session.send(frotz_in)
            except Exception as e:
                self.crash(e)

    def _handle_output(self):
        while not self._stop_requested:
            try:
                frotz_out = self._frotz_session.recv()
                self._slack_session.send(frotz_out)
            except pexpect.TIMEOUT:
                continue
            except Exception as e:
                self.crash(e)


def main():
    config = ConfigParser()
    config.read('frotzlack.conf')
    GameMaster(config)

if __name__ == "__main__":
    main()
