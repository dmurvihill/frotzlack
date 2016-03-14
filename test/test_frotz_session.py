import mock
import pytest
from multiprocessing.pool import ThreadPool
from threading import Lock, Thread

from frotzlack import FrotzSession


SAVE_NAME_PROMPT = "Please enter a filename []: \r\n"
SAVE_NAME_PROMPT_NONEMPTY_DEFAULT = "Please enter a filename [zork.sav]: \r\n"
SAVE_OVERWRITE_PROMPT = "Overwrite existing file? \r\n"
SAVE_SUCCESS_CONFIRMATION = "Ok.\r\n"
SAVE_FAILURE_NOTIFICATION = "Failed.\r\n"


@pytest.fixture
def mock_frotz(monkeypatch):
    frotz = mock.Mock()
    monkeypatch.setattr('pexpect.spawn', lambda x: frotz)
    return frotz


@pytest.fixture
def frotz_session(mock_frotz):
    return FrotzSession('frotz', 'zork', mock.Mock())


def test_save(frotz_session, mock_frotz):
    """
    The FrotzSession 'save' command should send the right messages to save a
    game.
    """
    mock_frotz.readline.side_effect = [SAVE_NAME_PROMPT,
                                       SAVE_SUCCESS_CONFIRMATION]

    frotz_session.save("filename.sav")

    call1 = (("save",), {})
    call2 = (("filename.sav",), {})
    expected_frotz_messages = (call1, call2)

    assert mock_frotz.readline.call_count == 2
    _check_string_calls(mock_frotz.sendline, expected_frotz_messages)


def test_save_with_nonempty_default_filename(frotz_session, mock_frotz):
    """
    The FrotzSession 'save' command should gracefully handle non-empty
    default file names from the file name prompt.
    """
    mock_frotz.readline.side_effect = [SAVE_NAME_PROMPT_NONEMPTY_DEFAULT,
                                       SAVE_SUCCESS_CONFIRMATION]

    frotz_session.save("filename.sav")

    call1 = (("save",), {})
    call2 = (("filename.sav",), {})
    expected_frotz_messages = (call1, call2)

    assert mock_frotz.readline.call_count == 2
    _check_string_calls(mock_frotz.sendline, expected_frotz_messages)


def test_save_with_overwrite(frotz_session, mock_frotz):
    """
    The FrotzSession 'save' command should be able to overwrite files if
    needed.
    """
    mock_frotz.readline.side_effect = [SAVE_NAME_PROMPT_NONEMPTY_DEFAULT,
                                       SAVE_OVERWRITE_PROMPT,
                                       SAVE_SUCCESS_CONFIRMATION]

    frotz_session.save("zork.sav")

    call1 = (("save",), {})
    call2 = (("zork.sav",), {})
    call3 = (("yes",), {})
    expected_frotz_messages = (call1, call2, call3)

    assert mock_frotz.readline.call_count == 3
    _check_string_calls(mock_frotz.sendline, expected_frotz_messages)


def test_unrecognized_save_name_prompt(frotz_session, mock_frotz):
    """
    The FrotzSession 'save' command should nope out if it sees unexpected
    messages from the Frotz process.
    """
    mock_frotz.readline.return_value = "this message is unexpected"

    with pytest.raises(FrotzSession.FrotzError):
        frotz_session.save("filename.sav")


def test_unrecognized_save_confirmation(frotz_session, mock_frotz):
    """
    The FrotzSession 'save' command should nope out if it sees unexpected
    messages from the Frotz process.
    """
    mock_frotz.readline.side_effect = [SAVE_NAME_PROMPT,
                                       SAVE_FAILURE_NOTIFICATION]

    with pytest.raises(FrotzSession.FrotzError):
        frotz_session.save("filename.sav")


def test_send_during_save(frotz_session, mock_frotz):
    """
    Calling send() while a save() is in progress must not interrupt the save.

    NOTE: This test doesn't always fail even when it should. Verifying threaded
    code is hard :-(.
    """

    class ReadLineWithContention:
        outputs = (x for x in [SAVE_NAME_PROMPT, SAVE_SUCCESS_CONFIRMATION])

        def __init__(self, contender):
            self._index = 0
            self._contender = contender

        def readline(self):
            self._contender.notify_turn()
            output = ReadLineWithContention.outputs.next()
            return output

    sender = _Contender(lambda: frotz_session.send("Hello."))
    mock_frotz.readline.side_effect = ReadLineWithContention(sender).readline
    sender.start()
    try:
        frotz_session.save("filename.sav")
    finally:
        sender.stop()

    #  Ensure the save messages were sent consecutively
    calls = mock_frotz.sendline.call_args_list
    save_index = calls.index((("save",), {}))
    assert calls[save_index + 1] == (("filename.sav",), {})


def test_recv_during_save(frotz_session, mock_frotz):
    """
    Calling recv() while a save() is in progress does not interrupt the save.
    """
    recver = _Contender(lambda: frotz_session.recv())

    def sendline(line):
        recver.notify_turn()
    mock_frotz.sendline.side_effect = sendline
    mock_frotz.readline.side_effect = [SAVE_NAME_PROMPT,
                                       SAVE_SUCCESS_CONFIRMATION]
    recver.start()
    try:
        frotz_session.save("filename.sav")
    finally:
        recver.stop()

    call1 = (("save",), {})
    call2 = (("filename.sav",), {})
    expected_calls = (call1, call2)
    _check_string_calls(mock_frotz.sendline, expected_calls)


class _Contender(Thread):
    """
    Tries to perform tasks in the middle of other tasks' critical regions, to
    test for deadlocks.
    """
    def __init__(self, action):
        self._action = action
        self._stop_requested = False
        self._turn_start_lock = Lock()
        self._turn_finish_lock = Lock()
        self._action_start_lock = Lock()
        self._turn_start_lock.acquire()
        self._turn_finish_lock.acquire()
        self._pool = ThreadPool()
        Thread.__init__(self, target=self._take_turns, name="contender")

    def notify_turn(self):
        """Block until the contender has taken a turn."""
        # start the turn
        self._turn_start_lock.release()
        # wait for the turn to finish
        self._turn_finish_lock.acquire()

    def stop(self):
        self._stop_requested = True
        self._turn_start_lock.release()
        self._pool.close()
        self._pool.join()
        Thread.join(self)

    def _start_action(self):
        self._action_start_lock.release()
        self._action()

    def _take_turns(self):
        while True:
            # notify the other thread the turn is finished
            self._turn_start_lock.acquire()
            if self._stop_requested:
                # take both locks and ride off into the sunset
                break
            # wait for the next turn
            self._pool.apply_async(self._start_action, ())
            # wait for confirmation the action thread was scheduled
            self._action_start_lock.acquire()
            # notify the other thread the turn is finished
            self._turn_finish_lock.release()


def _check_string_calls(mock_function, expected_calls):
    for (i, j) in zip(mock_function.call_args_list, expected_calls):
        assert i == j
