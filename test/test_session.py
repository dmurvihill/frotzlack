from mock import Mock

import frotzlack
from frotzlack import Session


def test_kill():
    """
    When killed, the session politely notifies the user that the
    session is stopping and then kills the Slack and Frotz sessions.
    """
    slack_session = Mock()
    frotz_session = Mock()
    session = Session(slack_session, frotz_session, "zork.sav")
    session.kill()

    expected_slack_send_call = ('send', (frotzlack.SERVER_SHUTDOWN_MSG,))
    assert expected_slack_send_call in slack_session.method_calls
    assert frotz_session.kill.called


def test_game_input_passthrough():
    """
    Upon receiving normal game input, the session passes it along to the
    Frotz session.
    """
    frotz_session = Mock()
    session = Session(Mock(), frotz_session, "zork.sav")

    try:
        session.notify_input("test1")
        session.notify_input("test2")

        expected_calls = [('send', ('test1',)), ('send', ('test2',))]
        actual_calls = frotz_session.method_calls
        assert all(call in actual_calls for call in expected_calls)

    finally:
        session.kill()


def test_game_input_save():
    """
    Upon receiving a save command, the session calls the Frotz session's
    save method.
    """
    frotz_session_1 = Mock()
    frotz_session_2 = Mock()
    slack_session = Mock()
    session1 = Session(slack_session, frotz_session_1, "zork.sav")
    session2 = Session(Mock(), frotz_session_2, "zork2.sav")

    try:
        session1.notify_input("save")
        session2.notify_input("save")
        expected_slack_call = ("send", (frotzlack.SAVE_SUCCESS_MSG,))
        expected_frotz1_call = ("save", ("zork.sav",))
        expected_frotz2_call = ("save", ("zork2.sav",))
        unexpected_frotz_call = ("send", ("save",))
        assert expected_frotz1_call in frotz_session_1.method_calls
        assert expected_frotz2_call in frotz_session_2.method_calls
        assert expected_slack_call in slack_session.method_calls
        assert unexpected_frotz_call not in frotz_session_1.method_calls

    finally:
        session1.kill()
        session2.kill()


def test_game_input_save_failure():
    """
    When the Frotz session fails to save, the session notifies the user
    with a friendly error message.
    """
    frotz_session = Mock()
    slack_session = Mock()
    frotz_session.save.side_effect = Exception("Test exception")

    session = Session(slack_session, frotz_session, 'zork.sav')
    try:
        session.notify_input('save')

        expected_slack_call = ('send', (frotzlack.SAVE_FAILURE_MSG,))
        assert expected_slack_call in slack_session.method_calls

    finally:
        session.kill()


def test_game_input_load():
    """
    Upon receiving a load command, the server politely refuses.
    """
    slack_session = Mock()

    session = Session(slack_session, Mock(), 'zork.sav')
    try:
        session.notify_input('load')

        expected_slack_call = ('send', (frotzlack.LOAD_NOT_IMPL_MSG,))
        assert expected_slack_call in slack_session.method_calls

    finally:
        session.kill()


def test_game_input_quit():
    """
    Upon receiving a quit command, the server politely refuses.
    """
    slack_session = Mock()

    session = Session(slack_session, Mock(), 'zork.sav')
    try:
        session.notify_input('quit')

        expected_slack_call = ('send', (frotzlack.QUIT_NOT_IMPL_MSG,))
        assert expected_slack_call in slack_session.method_calls

    finally:
        session.kill()
