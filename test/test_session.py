from frotzlack import Session
from mock import Mock

def test_save(monkeypatch):
    """
    The Session save() command calls the FrotzSession save() command
    """
    frotz_session = Mock()
    session = Session(Mock(), frotz_session, "zork.sav")

    try:
        session.save()

        expected_calls = [('save', ('zork.sav',))]
        actual_calls = frotz_session.method_calls

        # There will be calls to send() and recv(), but we don't care
        actual_calls = filter(lambda call: call[0] != 'send', actual_calls)
        actual_calls = filter(lambda call: call[0] != 'recv', actual_calls)

        assert actual_calls == expected_calls

    finally:
        session.kill()


def test_say():
    """
    The Session say() command calls the SlackSession send() command
    """
    slack_session = Mock()
    session = Session(slack_session, Mock(), "zork.sav")

    try:
        session.say("test1")
        session.say("test2")

        expected_calls = [('send', ('test1',)), ('send', ('test2',))]
        actual_calls = slack_session.method_calls

        # There will be calls to recv(), but we don't care
        actual_calls = filter(lambda call: call[0] != 'recv', actual_calls)

        assert all(call in actual_calls for call in expected_calls)

    finally:
        session.kill()
